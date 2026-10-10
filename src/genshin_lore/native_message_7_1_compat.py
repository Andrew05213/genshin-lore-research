"""Historical Native v1 routed surface over 7.1 Native Message evidence.

This adapter keeps exact 7.1 occurrence identities; it does not consult 7.0 data.
"""
from __future__ import annotations

import base64
import json

from .family_evidence import FamilyEvidenceResult
from .native_message_7_1_runtime import NativeMessage71Runtime

BUDGET = 262144


def _token(scope: dict, key: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({'scope': scope, 'last': key}, sort_keys=True,
                                           separators=(',', ':')).encode()).decode()


def _decode(token: str | None, scope: dict) -> str | None:
    if token is None:
        return None
    try:
        value = json.loads(base64.urlsafe_b64decode(token.encode()))
    except Exception as exc:
        raise ValueError('invalid cursor') from exc
    if value.get('scope') != scope or not isinstance(value.get('last'), str):
        raise ValueError('cursor scope mismatch')
    return value['last']


def _wire(result: FamilyEvidenceResult) -> int:
    return (3 * len(json.dumps(json.dumps(result.envelope(), ensure_ascii=True, separators=(',', ':')),
                               ensure_ascii=True, separators=(',', ':')).encode()) + 8192)


class NativeMessageVersionedProvider:
    family = 'native_archive'
    family_contract_version = 'native-archive-evidence-v1'

    def __init__(self, source, artifact, manifest):
        self.base = NativeMessage71Runtime(source, artifact, manifest)
        self.conn = self.base.db
        self.run = self.base.run
        self.coverage = 'complete declared 7.1 Native Message/Notify/Catalog physical-text projection'
        self._historical_owner_aliases = {}
        for declaration in self.base.scope['sources']:
            for owner in declaration['text_owners']:
                if owner.get('historical_field'):
                    self._historical_owner_aliases[owner['historical_field']] = owner['field']

    def close(self):
        self.base.close()

    def _result(self, data, status='ok', run=None):
        self.base._eligible(self.run, self.base.contract)
        return FamilyEvidenceResult(status, self.family, run or self.run,
                                    self.family_contract_version, self.coverage, True, data)

    def _eligible(self, run, contract):
        return self.base._eligible(run, self.base.contract) and contract == self.family_contract_version

    def _bounded(self, records, scope, limit):
        kind = scope['record_kind']
        delivered = []
        for row in records[:limit]:
            trial = delivered + [row]
            more = len(records) > len(trial)
            data = {'record_kind': kind, 'items': trial, 'delivered_count': len(trial),
                    'next_cursor': _token(scope, trial[-1]['id']) if more else None,
                    'scope_complete': not more, 'partial': more}
            if _wire(self._result(data)) > BUDGET:
                if not delivered:
                    raise ValueError('single record exceeds inline limit')
                break
            delivered = trial
        more = len(records) > len(delivered)
        return self._result({'record_kind': kind, 'items': delivered, 'delivered_count': len(delivered),
                             'next_cursor': _token(scope, delivered[-1]['id']) if more and delivered else None,
                             'scope_complete': not more, 'partial': more})

    def capability(self):
        self.base._eligible(self.run, self.base.contract)
        release = self.base.manifest['release']
        table_names = {'native71_row': 'native_archive_container', 'native71_slot': 'native_archive_slot',
                       'native71_text': 'native_archive_text', 'native71_relation': 'native_archive_relation',
                       'native71_candidate': 'native_archive_candidate'}
        counts = {table_names[key]: value for key, value in release['counts'].items()}
        return {'family': self.family, 'family_contract_version': self.family_contract_version,
                'evidence_available': True, 'evidence_only': True,
                'supported_operations': ['list_family_evidence', 'get_family_evidence', 'search_family_text'],
                'supported_filters': {'list': ['boundary', 'container_kind'],
                                      'search': ['boundary', 'locale', 'owner_field']},
                'pagination_contract': {'list': 'bounded deterministic container pages',
                                        'search': 'bounded deterministic literal-hit pages',
                                        'get': 'one complete physical container or explicit oversized failure',
                                        'cursor_scope': ['family', 'contract', 'run', 'record_kind', 'filters', 'query'],
                                        'legal_empty': {'items': [], 'delivered_count': 0,
                                                        'next_cursor': None, 'scope_complete': True, 'partial': False},
                                        'wire_budget_bytes': BUDGET},
                'eligible_runs': [{'ingest_run_id': self.run, 'parser_build_id': release['parser_build_id'],
                                   'snapshot_id': release['source_snapshot'], 'counts': counts,
                                   'coverage': self.coverage,
                                   'corpus_complete': True, 'product_accepted': False}],
                'default_run': False, 'latest_run': False, 'sibling_fallback': False,
                'historical_body_copied': False}

    def list_evidence(self, *, ingest_run_id, family_contract_version, filters=None, cursor=None, limit=50):
        if not self._eligible(ingest_run_id, family_contract_version):
            return self._result({'status': 'NotFound'}, 'not_found', ingest_run_id)
        selected = filters or {}
        if (not isinstance(selected, dict) or set(selected) - {'boundary', 'container_kind'}
                or type(limit) is not int or not 1 <= limit <= 100):
            raise ValueError('filters/limit')
        scope = {'run': self.run, 'contract': self.family_contract_version,
                 'record_kind': 'containers', 'filters': dict(sorted(selected.items()))}
        after = _decode(cursor, scope)
        if selected.get('container_kind') and selected['container_kind'] != 'physical_row':
            return self._bounded([], scope, limit)
        sql = ('SELECT id,boundary,source_path,ordinal,pointer,native_id_field,native_id '
               'FROM native71_row WHERE run_id=?')
        args = [self.run]
        if selected.get('boundary'):
            sql += ' AND boundary=?'; args.append(selected['boundary'])
        if after:
            sql += ' AND id>?'; args.append(after)
        records = []
        for item in self.conn.execute(sql + ' ORDER BY id LIMIT ?', (*args, limit + 1)):
            records.append({'id': item['id'], 'boundary': item['boundary'], 'container_kind': 'physical_row',
                            'source_path': item['source_path'], 'ordinal': item['ordinal'],
                            'pointer': item['pointer'], 'native_id_field': item['native_id_field'],
                            'native_id': item['native_id'], 'reuse_family': None, 'reuse_contract': None,
                            'reuse_parser': None, 'reuse_run': None, 'reuse_occurrence_id': None})
        return self._bounded(records, scope, limit)

    def get_evidence(self, *, ingest_run_id, family_contract_version, occurrence_id):
        if not self._eligible(ingest_run_id, family_contract_version):
            return self._result({'status': 'NotFound'}, 'not_found', ingest_run_id)
        raw = self.base.get(run=self.run, contract=self.base.contract, occurrence_id=occurrence_id)
        if raw.get('status') == 'NotFound':
            return self._result({'status': 'NotFound'}, 'not_found')
        container = dict(raw['container'])
        container['container_kind'] = 'physical_row'
        container.update({key: None for key in ('reuse_family', 'reuse_contract', 'reuse_parser',
                                                'reuse_run', 'reuse_occurrence_id')})
        slots = []
        for item in raw['slots']:
            slot = dict(item)
            slot['container_id'] = slot.pop('row_id')
            slot.pop('historical_owner_field')
            slot['status'] = 'ambiguous' if slot['status'] == 'resolved_candidate_set' else slot['status']
            slot['candidate_count'] = slot.pop('selected_candidate_count')
            slots.append(slot)
        texts = []
        for item in raw['text_candidates']:
            if item['selected']:
                entry = dict(item)
                for key in ('tier', 'selected'):
                    entry.pop(key)
                texts.append(entry)
        relations = []
        for item in raw['relations']:
            relation = dict(item)
            relation['container_id'] = relation.pop('row_id')
            relation['status'] = 'ambiguous' if relation['status'] == 'resolved_candidate_set' else relation['status']
            relation['candidates'] = []
            for item in self.conn.execute('SELECT * FROM native71_candidate WHERE relation_id=? ORDER BY id',
                                          (relation['id'],)):
                relation['candidates'].append({
                    'id': item['id'], 'relation_id': item['relation_id'],
                    'target_source_path': item['target_source_path'],
                    'target_source_pointer': item['target_source_pointer'],
                    'target_native_id': item['target_native_id'], 'target_family': item['target_family'],
                    'target_contract': item['target_contract'], 'target_parser': item['target_parser'],
                    'target_run': item['target_run'], 'target_occurrence_id': item['target_occurrence_id']})
            relations.append(relation)
        result = self._result({'container': container, 'text_slots': slots, 'text_candidates': texts,
                               'relations': relations, 'complete_physical_container': True,
                               'historical_body_embedded': False})
        if _wire(result) > BUDGET:
            raise ValueError('physical container exceeds inline limit')
        return result

    def search_text(self, *, ingest_run_id, family_contract_version, query, filters=None, cursor=None, limit=50):
        if not self._eligible(ingest_run_id, family_contract_version):
            return self._result({'status': 'NotFound'}, 'not_found', ingest_run_id)
        selected = filters or {}
        if (not isinstance(selected, dict) or set(selected) - {'boundary', 'locale', 'owner_field'}
                or not isinstance(query, str) or not query or type(limit) is not int or not 1 <= limit <= 100):
            raise ValueError('filters/query/limit')
        scope = {'run': self.run, 'contract': self.family_contract_version, 'record_kind': 'search',
                 'query': query, 'filters': dict(sorted(selected.items()))}
        after = _decode(cursor, scope)
        sql = ('SELECT t.id,t.slot_id,t.source_path,t.pointer,t.raw_text,t.raw_text_sha256,'
               's.row_id AS container_id,s.owner_field,s.locale,c.boundary FROM native71_text t '
               'JOIN native71_slot s ON s.id=t.slot_id JOIN native71_row c ON c.id=s.row_id '
               'WHERE c.run_id=? AND t.selected=1 AND instr(t.raw_text,?)>0')
        args = [self.run, query]
        for key in ('boundary', 'locale', 'owner_field'):
            if selected.get(key):
                sql += ' AND ' + ('c.' if key == 'boundary' else 's.') + key + '=?'
                args.append(self._historical_owner_aliases.get(selected[key], selected[key])
                            if key == 'owner_field' else selected[key])
        if after:
            sql += ' AND t.id>?'; args.append(after)
        records = [dict(item) for item in self.conn.execute(sql + ' ORDER BY t.id LIMIT ?', (*args, limit + 1))]
        return self._bounded(records, scope, limit)
