"""Historical Event v3 surface over the exact 7.1 component-17 candidate.

This is a family-specific output adapter, not a cross-snapshot data fallback.
"""
from __future__ import annotations

import base64
import json

from .event_native_text_7_1_runtime import EventNativeText71Runtime
from .family_evidence import FamilyEvidenceResult

BUDGET = 262144


def _cursor(scope: dict, last: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({'scope': scope, 'last': last}, sort_keys=True,
                                           separators=(',', ':')).encode()).decode()


def _after(value: str | None, scope: dict) -> str | None:
    if value is None:
        return None
    try:
        record = json.loads(base64.urlsafe_b64decode(value.encode()))
    except Exception as exc:
        raise ValueError('invalid cursor') from exc
    if record.get('scope') != scope or not isinstance(record.get('last'), str):
        raise ValueError('cursor scope mismatch')
    return record['last']


def _filters(value: dict | None, allowed: set[str]) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(k not in allowed or not isinstance(v, str) or not v
                                          for k, v in value.items()):
        raise ValueError('filters')
    return dict(sorted(value.items()))


def _wire(result: FamilyEvidenceResult) -> int:
    payload = json.dumps(json.dumps(result.envelope(), ensure_ascii=True, separators=(',', ':')),
                         ensure_ascii=True, separators=(',', ':')).encode()
    return 3 * len(payload) + 8192


class Event17VersionedProvider:
    family = 'event_narrative_archive'
    family_contract_version = 'event-narrative-evidence-v3'

    def __init__(self, source, artifact, manifest):
        self.base = EventNativeText71Runtime(source, artifact, manifest)
        self.run = self.base.run
        self.conn = self.base.db
        self.coverage_label = 'complete declared native-text archive component-17 physical/text/local relation projection'
        self._relations = {}
        for declaration in self.base.scope['sources']:
            for rule in declaration.get('internal_relations', []):
                key = (declaration['path'], rule['relation_kind'], rule['field'])
                if key in self._relations:
                    raise ValueError('Event v3 duplicate relation declaration')
                self._relations[key] = rule.get('candidate_namespace',
                                                'event_wave_physical_rows:' + rule['target_kind'])

    def close(self):
        self.base.close()

    def _result(self, data, status='ok', run=None):
        self.base._stable()
        return FamilyEvidenceResult(status, self.family, run or self.run, self.family_contract_version,
                                    self.coverage_label, True, data)

    def _eligible(self, run, contract):
        return self.base._eligible(run, self.base.contract) and contract == self.family_contract_version

    def _bounded(self, records, scope, limit):
        delivered = []
        for item in records[:limit]:
            trial = delivered + [item]
            more = len(records) > len(trial)
            data = {'items': trial, 'delivered_count': len(trial),
                    'next_cursor': _cursor(scope, trial[-1]['id']) if more else None,
                    'scope_complete': not more, 'partial': more}
            if _wire(self._result(data)) > BUDGET:
                if not delivered:
                    raise ValueError('single record exceeds inline limit')
                break
            delivered = trial
        more = len(records) > len(delivered)
        result = self._result({'items': delivered, 'delivered_count': len(delivered),
                               'next_cursor': _cursor(scope, delivered[-1]['id']) if more and delivered else None,
                               'scope_complete': not more, 'partial': more})
        if _wire(result) > BUDGET:
            raise ValueError('bounded response exceeds inline limit')
        return result

    def capability(self):
        release = self.base.manifest['release']
        self.base._stable()
        historical_counts = {
            {'event71_row': 'event_narrative_row', 'event71_slot': 'event_narrative_slot',
             'event71_text': 'event_narrative_text', 'event71_relation': 'event_narrative_relation',
             'event71_candidate': 'event_narrative_relation_candidate'}[key]: value
            for key, value in release['counts'].items()}
        historical_states = {('ambiguous' if key == 'resolved_candidate_set' else key): value
                             for key, value in release['text_states'].items()}
        return {'family': self.family, 'family_contract_version': self.family_contract_version,
                'evidence_only': True, 'evidence_available': True,
                'supported_operations': ['list_family_evidence', 'get_family_evidence', 'search_family_text'],
                'supported_filters': {'list': ['boundary', 'native_id'],
                                      'search': ['boundary', 'locale', 'owner_field']},
                'pagination_contract': {'kind': 'scope-bound keyset', 'full_response_budget_bytes': BUDGET,
                                        'legal_empty_page': True, 'default_run': None, 'latest_run': None,
                                        'sibling_fallback': False},
                'container_scope': 'one physical event narrative source row',
                'coverage_semantics': {'coverage': self.coverage_label, 'relations_declared':
                                       release['counts']['event71_relation'], 'full_activity': False, 'full_7_0': False},
                'eligible_runs': [{'ingest_run_id': self.run, 'parser_build_id': release['parser_build_id'],
                                   'snapshot_id': release['source_snapshot'], 'coverage': self.coverage_label,
                                   'counts': historical_counts, 'text_state_counts': historical_states,
                                   'artifact': self.base.manifest['artifact'], 'corpus_complete': True,
                                   'product_accepted': False}],
                'default_run': False, 'latest_run': False}

    def list_evidence(self, *, ingest_run_id, family_contract_version, filters=None, cursor=None, limit=50):
        if not self._eligible(ingest_run_id, family_contract_version):
            return self._result({'status': 'NotFound'}, 'not_found', ingest_run_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('limit')
        selected = _filters(filters, {'boundary', 'native_id'})
        scope = {'run': self.run, 'contract': self.family_contract_version, 'operation': 'list', 'filters': selected}
        position = _after(cursor, scope)
        sql = ('SELECT id,boundary,source_path,ordinal,pointer,native_id_field,native_id '
               'FROM event71_row WHERE run_id=?')
        args = [self.run]
        for name in ('boundary', 'native_id'):
            if name in selected:
                sql += ' AND ' + name + '=?'; args.append(selected[name])
        if position:
            sql += ' AND id>?'; args.append(position)
        records = [dict(item) for item in self.conn.execute(sql + ' ORDER BY id LIMIT ?', (*args, limit + 1))]
        return self._bounded(records, scope, limit)

    def get_evidence(self, *, ingest_run_id, family_contract_version, occurrence_id):
        if not self._eligible(ingest_run_id, family_contract_version):
            return self._result({'status': 'NotFound'}, 'not_found', ingest_run_id)
        raw = self.base.get(run=self.run, contract=self.base.contract, occurrence_id=occurrence_id)
        if raw.get('status') == 'NotFound':
            return self._result({'status': 'NotFound'}, 'not_found')
        row = dict(raw['container']); row.pop('kind')
        slots = []
        for item in raw['slots']:
            entry = dict(item)
            entry['status'] = 'ambiguous' if entry['status'] == 'resolved_candidate_set' else entry['status']
            entry['candidate_count'] = entry.pop('selected_candidate_count')
            slots.append(entry)
        texts = []
        for item in raw['text_candidates']:
            if item['selected']:
                entry = dict(item); entry.pop('tier'); entry.pop('selected'); texts.append(entry)
        relations = []
        for item in raw['relations']:
            entry = {k: v for k, v in item.items() if k not in ('target_kind', 'candidates')}
            entry['candidate_namespace'] = self._relations[(row['source_path'], entry['relation_kind'],
                                                             entry['source_field'])]
            if entry['status'] == 'resolved_candidate_set':
                entry['status'] = 'ambiguous'
            entry['candidates'] = []
            for candidate in item['candidates']:
                target = self.conn.execute('SELECT * FROM event71_row WHERE id=?',
                                           (candidate['target_row_id'],)).fetchone()
                if target is None or target['source_path'] != candidate['target_source_path'] or target['pointer'] != candidate['target_source_pointer']:
                    raise ValueError('Event v3 candidate target drift')
                entry['candidates'].append({'id': candidate['id'], 'relation_id': candidate['relation_id'],
                                            'target_source_path': candidate['target_source_path'],
                                            'target_source_pointer': candidate['target_source_pointer'],
                                            'target_native_id': candidate['target_native_id'],
                                            'target_family': self.family, 'target_contract': self.family_contract_version,
                                            'target_parser_build_id': self.base.manifest['release']['parser_build_id'],
                                            'target_run_id': self.run, 'target_occurrence_id': candidate['target_row_id']})
            relations.append(entry)
        result = self._result({'container': row, 'text_slots': slots, 'texts': texts,
                               'relations': relations, 'complete': True, 'target_bodies_embedded': False})
        if _wire(result) > BUDGET:
            raise ValueError('single event container exceeds inline limit')
        return result

    def search_text(self, *, ingest_run_id, family_contract_version, query, filters=None, cursor=None, limit=50):
        if not self._eligible(ingest_run_id, family_contract_version):
            return self._result({'status': 'NotFound'}, 'not_found', ingest_run_id)
        if not isinstance(query, str) or not query or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('query/limit')
        selected = _filters(filters, {'boundary', 'locale', 'owner_field'})
        scope = {'run': self.run, 'contract': self.family_contract_version, 'operation': 'search',
                 'filters': selected, 'query': query}
        position = _after(cursor, scope)
        sql = ('SELECT t.id,t.slot_id,t.source_path,t.pair_ordinal,t.pointer,t.raw_text,t.raw_text_sha256,'
               's.row_id AS container_id,s.locale,s.owner_field,r.boundary FROM event71_text t '
               'JOIN event71_slot s ON s.id=t.slot_id JOIN event71_row r ON r.id=s.row_id '
               'WHERE r.run_id=? AND t.selected=1 AND instr(t.raw_text,?)>0')
        args = [self.run, query]
        for name in ('boundary', 'locale', 'owner_field'):
            if name in selected:
                sql += ' AND ' + ('r.' if name == 'boundary' else 's.') + name + '=?'
                args.append(selected[name])
        if position:
            sql += ' AND t.id>?'; args.append(position)
        records = [dict(item) for item in self.conn.execute(sql + ' ORDER BY t.id LIMIT ?', (*args, limit + 1))]
        return self._bounded(records, scope, limit)
