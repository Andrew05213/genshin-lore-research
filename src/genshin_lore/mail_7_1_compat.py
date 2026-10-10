"""Versioned historical Mail v1 surface over sealed 7.1 physical evidence.

The old Alchemy field name is a query alias only. Its new physical field and
unresolved semantic owner state are never rewritten in evidence.
"""
from __future__ import annotations

import base64
import json

from .family_evidence import FamilyEvidenceResult
from .mail_7_1_runtime import Mail71Runtime

BUDGET = 262144
OLD_ALCHEMY_FIELD = 'ODPMDNBBMAB'
NEW_ALCHEMY_FIELD = 'AICANPKILKN'


def _token(scope: dict, last: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({'scope': scope, 'last': last}, sort_keys=True,
                                               separators=(',', ':')).encode()).decode()


def _after(value: str | None, scope: dict) -> str | None:
    if value is None:
        return None
    try:
        decoded = json.loads(base64.urlsafe_b64decode(value.encode()))
    except Exception as exc:
        raise ValueError('Mail v1 cursor') from exc
    if decoded.get('scope') != scope or not isinstance(decoded.get('last'), str):
        raise ValueError('Mail v1 cursor scope mismatch')
    return decoded['last']


def _filters(value: dict | None, allowed: set[str]) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(key not in allowed or not isinstance(item, str) or not item
                                               for key, item in value.items()):
        raise ValueError('Mail v1 filters')
    return dict(sorted(value.items()))


class Mail71VersionedProvider:
    family = 'mail_narrative_archive'
    family_contract_version = 'mail-narrative-evidence-v1'

    def __init__(self, artifact, manifest, source):
        self.base = Mail71Runtime(artifact, manifest, source)
        self.run = self.base.run
        self.conn = self.base.db
        self.coverage = 'complete_frozen_mail_template_birthday_schedule_and_alchemy_letter_projection'

    def close(self) -> None:
        self.base.close()

    def _result(self, data: dict, status='ok', run=None) -> FamilyEvidenceResult:
        self.base._stable()
        return FamilyEvidenceResult(status, self.family, run or self.run,
                                    self.family_contract_version, self.coverage, True, data)

    def _eligible(self, run: str, contract: str) -> bool:
        self.base._stable()
        return run == self.run and contract == self.family_contract_version

    def _page(self, rows: list[dict], scope: dict, limit: int) -> FamilyEvidenceResult:
        delivered: list[dict] = []
        for row in rows[:limit]:
            trial = delivered + [row]
            if len(json.dumps(trial, ensure_ascii=False, separators=(',', ':')).encode()) > BUDGET:
                if not delivered:
                    raise ValueError('Mail v1 single record exceeds inline limit')
                break
            delivered = trial
        more = len(rows) > len(delivered)
        return self._result({'items': delivered, 'delivered_count': len(delivered),
                             'next_cursor': _token(scope, delivered[-1]['id']) if more and delivered else None,
                             'scope_complete': not more, 'partial': more})

    def capability(self) -> dict:
        self.base._stable()
        release = self.base.manifest['release']
        return {'family': self.family, 'family_contract_version': self.family_contract_version,
                'evidence_only': True, 'evidence_available': True,
                'supported_operations': ['list_family_evidence', 'get_family_evidence', 'search_family_text'],
                'supported_filters': {'list': ['source_kind', 'native_id'],
                                      'search': ['locale', 'owner_field']},
                'pagination_contract': 'exact run/contract/operation/filter/query keyset; 262144-byte list/search budget',
                'container_scope': 'one physical Mail, BirthdayMail, or AlchemySimLetter row',
                'coverage_semantics': {'coverage': self.coverage, 'all_mailbox_gameplay': False, 'full_7_0': False},
                'eligible_runs': [{'ingest_run_id': self.run, 'parser_build_id': release['parser_build_id'],
                                   'snapshot_id': release['snapshot_id'], 'counts': release['counts'],
                                   'text_state_counts': release['text_state_counts'],
                                   'relation_state_counts': release['relation_state_counts'],
                                   'artifact': self.base.manifest['artifact'], 'corpus_complete': True,
                                   'product_accepted': False}],
                'default_run': False, 'latest_run': False, 'sibling_fallback': False,
                'alchemy_semantic_owner_mapping': 'unresolved'}

    def list_evidence(self, *, ingest_run_id, family_contract_version, filters=None, cursor=None, limit=50):
        if not self._eligible(ingest_run_id, family_contract_version):
            return self._result({'status': 'NotFound'}, 'not_found', ingest_run_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Mail v1 limit')
        selected = _filters(filters, {'source_kind', 'native_id'})
        scope = {'run': self.run, 'contract': self.family_contract_version, 'op': 'list', 'filters': selected}
        last = _after(cursor, scope)
        sql = 'SELECT id,source_kind,ordinal,native_id,source_path,pointer FROM mail_row WHERE run_id=?'
        args: list[object] = [self.run]
        for field in ('source_kind', 'native_id'):
            if field in selected:
                sql += ' AND ' + field + '=?'; args.append(selected[field])
        if last is not None:
            sql += ' AND id>?'; args.append(last)
        rows = [dict(row) for row in self.conn.execute(sql + ' ORDER BY id LIMIT ?', (*args, limit + 1))]
        return self._page(rows, scope, limit)

    def get_evidence(self, *, ingest_run_id, family_contract_version, occurrence_id):
        if not self._eligible(ingest_run_id, family_contract_version):
            return self._result({'status': 'NotFound'}, 'not_found', ingest_run_id)
        raw = self.base.get_evidence(ingest_run_id=self.run,
                                     family_contract_version=self.base.family_contract_version,
                                     occurrence_id=occurrence_id)
        if raw.status == 'not_found':
            return self._result({'status': 'NotFound'}, 'not_found')
        data = dict(raw.data)
        data['text_slots'] = [slot if slot['owner_field'] == NEW_ALCHEMY_FIELD
                              else {key: value for key, value in slot.items()
                                    if key not in ('field_continuity_status', 'semantic_owner_mapping')}
                              for slot in data['text_slots']]
        for slot in data['text_slots']:
            if slot['owner_field'] == NEW_ALCHEMY_FIELD:
                slot['raw_locator_continuity'] = 'proven_by_6_of_6_source_owned_id_alignment'
        for candidate in data['candidates']:
            candidate['runtime_locator'] = {'family': self.family,
                                            'family_contract_version': self.family_contract_version,
                                            'ingest_run_id': self.run,
                                            'occurrence_id': candidate['target_row_id']}
        if len(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()) > 1048576:
            raise ValueError('Mail v1 single container exceeds inline limit')
        return self._result(data)

    def search_text(self, *, ingest_run_id, family_contract_version, query,
                    filters=None, cursor=None, limit=50):
        if not self._eligible(ingest_run_id, family_contract_version):
            return self._result({'status': 'NotFound'}, 'not_found', ingest_run_id)
        if not isinstance(query, str) or not query or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Mail v1 query/limit')
        selected = _filters(filters, {'locale', 'owner_field'})
        scope = {'run': self.run, 'contract': self.family_contract_version,
                 'op': 'search', 'filters': selected, 'query': query}
        last = _after(cursor, scope)
        sql = ('SELECT t.id,t.slot_id,t.source_path,t.pair_ordinal,t.pointer,t.raw_text,t.raw_text_sha256,'
               's.row_id AS container_id,s.locale,s.owner_field FROM mail_text t '
               'JOIN mail_slot s ON s.id=t.slot_id JOIN mail_row r ON r.id=s.row_id '
               'WHERE r.run_id=? AND instr(t.raw_text,?)>0')
        args: list[object] = [self.run, query]
        for field in ('locale', 'owner_field'):
            if field in selected:
                sql += ' AND s.' + field + '=?'
                value = selected[field]
                args.append(NEW_ALCHEMY_FIELD if field == 'owner_field' and value == OLD_ALCHEMY_FIELD else value)
        if last is not None:
            sql += ' AND t.id>?'; args.append(last)
        rows = [dict(row) for row in self.conn.execute(sql + ' ORDER BY t.id LIMIT ?', (*args, limit + 1))]
        for row in rows:
            if row['owner_field'] == NEW_ALCHEMY_FIELD:
                row['semantic_owner_mapping'] = 'unresolved'
        return self._page(rows, scope, limit)


class MailArchiveMultiRunProvider:
    """Explicit-run router; no default or cross-snapshot fallback."""

    def __init__(self, *providers):
        if not providers or len({p.run for p in providers}) != len(providers):
            raise ValueError('unique explicit Mail runs required')
        self.providers = {p.run: p for p in providers}
        self.family = 'mail_narrative_archive'
        self.family_contract_version = 'mail-narrative-evidence-v1'

    def capability(self):
        capabilities = [provider.capability() for provider in self.providers.values()]
        return {**capabilities[0], 'eligible_runs': [run for item in capabilities for run in item['eligible_runs']]}

    def _provider(self, ingest_run_id):
        return self.providers.get(ingest_run_id)

    def _not_found(self, ingest_run_id):
        return FamilyEvidenceResult('not_found', self.family, ingest_run_id,
                                    self.family_contract_version, 'explicit Mail runs only', True,
                                    {'status': 'NotFound'})

    def list_evidence(self, *, ingest_run_id, family_contract_version, **kwargs):
        provider = self._provider(ingest_run_id)
        if provider is None or family_contract_version != self.family_contract_version:
            return self._not_found(ingest_run_id)
        return provider.list_evidence(ingest_run_id=ingest_run_id,
                                      family_contract_version=family_contract_version, **kwargs)

    def get_evidence(self, *, ingest_run_id, family_contract_version, **kwargs):
        provider = self._provider(ingest_run_id)
        if provider is None or family_contract_version != self.family_contract_version:
            return self._not_found(ingest_run_id)
        return provider.get_evidence(ingest_run_id=ingest_run_id,
                                     family_contract_version=family_contract_version, **kwargs)

    def search_text(self, *, ingest_run_id, family_contract_version, **kwargs):
        provider = self._provider(ingest_run_id)
        if provider is None or family_contract_version != self.family_contract_version:
            return self._not_found(ingest_run_id)
        return provider.search_text(ingest_run_id=ingest_run_id,
                                    family_contract_version=family_contract_version, **kwargs)
