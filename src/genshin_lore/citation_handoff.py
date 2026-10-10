"""Opt-in, connection-scoped citation transport. Never selects or edits claims.

Only server-side qualified results are registered. Private no-clobber receipts
let capture bind mappings to actual completed MCP calls, not a model's footer.
This is post-capture delivery, not a replacement for native client rendering.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import secrets

CONTRACT = 'research-citation-handoff-v1'
HANDLE = re.compile(r'\[H[0-9a-f]{16}:R[1-9][0-9]*:E[1-9][0-9]*\]')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class CitationHandoff:
    def __init__(self, directory=None):
        self.session = secrets.token_hex(8)
        self.directory = Path(directory) if directory else None
        self.counter = 0
        self._records = {}  # canonical bytes, not caller-owned mutable mappings

    def resolve(self, value):
        if not isinstance(value, str):
            raise ValueError('citation reference must be a string')
        if value.startswith('[H'):
            if not HANDLE.fullmatch(value) or value not in self._records:
                raise ValueError('unknown, expired or different-connection citation handle')
            return json.loads(self._records[value])
        return {'evidence_ref': value, 'observation_index': None, 'range': None}

    def attach(self, payload, operation, *, inspected_ref=None):
        if payload.get('status') != 'ok':
            return payload
        self.counter += 1
        request = 'R' + str(self.counter)
        result = copy.deepcopy(payload)
        records = []

        def visit(node, inherited=None):
            if isinstance(node, list):
                for child in node:
                    visit(child, inherited)
            elif isinstance(node, dict):
                inherited = node.get('evidence_ref', inherited)
                # Preview inspection's observation has its ref on the envelope.
                ref = node.get('evidence_ref') or (inherited if 'observation_index' in node else None)
                if ref and isinstance(ref, str):
                    handle = f'[H{self.session}:{request}:E{len(records)+1}]'
                    if handle in self._records:
                        raise ValueError('duplicate immutable handle namespace')
                    body = next((node[k] for k in ('raw_text', 'excerpt', 'text', 'snippet')
                                 if isinstance(node.get(k), str)), None)
                    position = node.get('text_range')
                    if position is None and 'start' in node and 'end' in node:
                        position = {k:node[k] for k in ('start','end','total_chars','end_reached') if k in node}
                    if position is None and body is not None:
                        start = max(0,node['match_span'][0]-80) if node.get('match_span') and 'snippet' in node else 0
                        position = {'start': start, 'end': start + len(body),
                                    'complete': not node.get('excerpt_truncated', node.get('truncated', 'snippet' in node))}
                    record = {'session': self.session, 'request': request, 'handle': handle,
                              'operation': operation, 'evidence_ref': ref,
                              'observation_index': node.get('observation_index'), 'range': position,
                              'citation_granularity': 'observation_view' if node.get('observation_index') is not None else ('text_view' if body is not None else 'container_or_locator'),
                              'quoted_text': body, 'quoted_text_sha256': hashlib.sha256(body.encode()).hexdigest() if body is not None else None,
                              'citation_label': node.get('citation_label'),
                              'source_result': copy.deepcopy(node),
                              'material_read': operation in ('read', 'query') or (operation == 'inspect' and body is not None),
                              'qualified_by_inspect': operation in ('query', 'inspect'),
                              'proof_semantics': 'unchanged source result; handles do not establish ownership or identity'}
                    records.append(record)
                    self._records[handle] = canonical(record)
                    node['citation_handle'] = handle
                # Navigation locators are addressable, not inspected material.
                if isinstance(node.get('target_evidence_ref'), str):
                    locator = {'evidence_ref': node['target_evidence_ref']}
                    visit(locator)
                    node['target_citation_handle'] = locator['citation_handle']
                for key, child in list(node.items()):
                    if key not in ('citation_handle', 'target_citation_handle'):
                        visit(child, inherited)

        visit(result, result.get('provenance', {}).get('evidence_ref'))
        receipt = {'contract': CONTRACT, 'session': self.session, 'request': request,
                   'operation': operation, 'result_sha256': digest(result),
                   'inspected_ref': inspected_ref, 'records': records}
        blob = canonical(receipt)
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f'{self.session}-{request}.json'
            with path.open('xb') as stream:
                stream.write(blob)
        result['citation_handoff'] = {'contract': CONTRACT, 'session': self.session,
                                     'request': request, 'receipt_sha256': hashlib.sha256(blob).hexdigest(),
                                     'delivery': 'explicit post-capture renderer; raw native answer retained',
                                     'instruction': 'Use supplied [H...:R...:E...] handles near claims. Inspect final material handles. No long-ref copying or invented mapping.'}
        return result


def bind_capture(trace, directory):
    """Read private receipts and bind them to THIS native session's real calls."""
    mappings, inspections, calls = {}, set(), []
    for call in trace['tool_calls']:
        payload = call.get('result')
        if not isinstance(payload, dict) or 'citation_handoff' not in payload:
            continue
        meta = payload['citation_handoff']
        if meta.get('contract') != CONTRACT or call['status'] != 'completed':
            raise ValueError('uncompleted or wrong-contract handoff')
        if not re.fullmatch('[0-9a-f]{16}', meta['session']) or not re.fullmatch('R[1-9][0-9]*',meta['request']):
            raise ValueError('malformed receipt scope')
        path = Path(directory) / f"{meta['session']}-{meta['request']}.json"
        blob = path.read_bytes()
        if hashlib.sha256(blob).hexdigest() != meta['receipt_sha256']:
            raise ValueError('private mapping drift')
        receipt = json.loads(blob)
        unsigned = {k:v for k,v in payload.items() if k != 'citation_handoff'}
        if (receipt['session'], receipt['request'], receipt['result_sha256']) != (meta['session'], meta['request'], digest(unsigned)):
            raise ValueError('handoff not bound to actual MCP result')
        if receipt['inspected_ref']:
            inspections.add(receipt['inspected_ref'])
        for record in receipt['records']:
            if record['handle'] in mappings:
                raise ValueError('duplicate immutable handle namespace')
            mappings[record['handle']] = {**record, 'native_call_id': call['item_id'], 'server': call['server'], 'tool': call['tool']}
        calls.append({'native_call_id': call['item_id'], **meta})
    result = {'contract': CONTRACT, 'capture_sha256': trace['stdout_sha256'],
              'calls': calls, 'mappings': mappings, 'inspected_refs': sorted(inspections)}
    result['binding_sha256'] = digest(result)
    return result


def render_answer(answer, bound, *, minimum_citations=1):
    if bound.get('binding_sha256') != digest({k:v for k,v in bound.items() if k != 'binding_sha256'}):
        raise ValueError('untrusted or modified capture binding')
    if not isinstance(answer, str) or not answer.strip():
        return {'status': 'NO_FINAL_ANSWER', 'model_answer_raw': answer,
                'rendered_answer': None, 'citation_resolution': []}
    used = list(dict.fromkeys(HANDLE.findall(answer)))
    malformed = [x for x in re.findall(r'\[H[^\]\n]*\]', answer) if not HANDLE.fullmatch(x)]
    errors = ['malformed handle: ' + x for x in malformed]
    if answer.count('[H') != len(HANDLE.findall(answer)):
        errors.append('malformed or truncated handle; no guessed recovery')
    resolved = []
    for handle in used:
        record = bound['mappings'].get(handle)
        if record is None:
            errors.append('unknown or out-of-capture handle: ' + handle)
        elif not record['material_read'] and not any(
                x['evidence_ref'] == record['evidence_ref'] and x['material_read']
                for x in bound['mappings'].values()):
            errors.append('discovery locator has not been read: ' + handle)
        elif not record['qualified_by_inspect'] and record['evidence_ref'] not in bound['inspected_refs']:
            errors.append('final material not inspected: ' + handle)
        else:
            resolved.append(record)
    if len(used) < minimum_citations:
        errors.append('missing required citations')
    if errors:
        return {'status': 'DELIVERY_FAILED', 'model_answer_raw': answer, 'rendered_answer': None,
                'errors': errors, 'citation_resolution': resolved}
    footer = '\n\n来源映射（程序交付；不证明附近陈述或候选归属）：\n'
    for record in resolved:
        footer += (f"\n{record['handle']} {record.get('citation_label') or '物理来源'}\n"
                   f"exact evidence_ref: {record['evidence_ref']}\n"
                   f"observation: {record['observation_index']}; range: {json.dumps(record['range'],ensure_ascii=False)}\n"
                   f"granularity: {record['citation_granularity']}\n"
                   f"source state: {json.dumps({k:record['source_result'][k] for k in ('locale','proof_status','role','owner_field','limitations','text_proof') if k in record['source_result']},ensure_ascii=False)}\n"
                   "来源资格与限制保持原工具结果；本映射不证明说话者、唯一归属、剧情顺序或全量。\n")
    return {'status': 'DELIVERED', 'model_answer_raw': answer, 'rendered_answer': answer + footer,
            'citation_resolution': resolved, 'citation_map': {x['handle']:x for x in resolved},
            'semantic_support': 'NOT_AUTOMATICALLY_CHECKED; independent nearby-claim review required'}
