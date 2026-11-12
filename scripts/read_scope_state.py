"""Caller-side, connection-local coverage for sequential MCP read_evidence calls.

This module does not read evidence, decode refs, inspect sources, or change the
server. Unknown/compact identities remain retryable. Only a contiguous chain of
successful, explicit read pages can complete a scope; equal text is irrelevant.
"""
from __future__ import annotations

import copy
import json
import re


HANDLE = re.compile(r'\[H([0-9a-f]{16}):R[1-9][0-9]*:E[1-9][0-9]*\]')
DEFAULTS = dict(view='physical', locale='CHS', detail='reading', before=0,
                after=0, limit=20)


class ReadState:
    """Use a fresh instance per connection. Never restore it from a trace."""

    def __init__(self):
        self.clear()

    def clear(self):
        self.aliases = {}
        self.scopes = {}
        self.events = []
        self.session = None

    def canonical(self, ref):
        return self.aliases.get(ref, ref)

    def learn(self, response, *, requested_ref=None):
        """Learn only successful, returned handle/exact-ref pairs, atomically."""
        if response.get('status') != 'ok':
            return
        session = response.get('citation_handoff', {}).get('session')
        if session and self.session and session != self.session:
            raise ValueError('different MCP connection; use a fresh ReadState')
        bindings = {}

        def bind(handle, exact):
            if not (isinstance(handle, str) and HANDLE.fullmatch(handle)
                    and isinstance(exact, str) and exact and not exact.startswith('[H')):
                return
            owner = HANDLE.fullmatch(handle).group(1)
            if (session or self.session) and owner != (session or self.session):
                raise ValueError('foreign connection handle in response')
            if handle in self.aliases and self.aliases[handle] != exact:
                raise ValueError('immutable handle was rebound to another exact ref')
            if handle in bindings and bindings[handle] != exact:
                raise ValueError('conflicting handle bindings in response')
            bindings[handle] = exact

        def visit(value):
            if isinstance(value, dict):
                if value.get('status') in ('failed', 'unavailable', 'invalid_request',
                                         'response_too_large', 'not_found', 'error'):
                    return
                for handle, ref in [('citation_handle', 'evidence_ref'),
                                    ('container_handle', 'container_ref'),
                                    ('provenance_handle', 'provenance_ref'),
                                    ('target_citation_handle', 'target_evidence_ref')]:
                    bind(value.get(handle), value.get(ref))
                nested = value.get('data')
                bind(value.get('selected_reference'), nested.get('evidence_ref') if isinstance(nested, dict) else None)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(response)
        bind(requested_ref, response.get('data', {}).get('evidence_ref'))
        self.aliases.update(bindings)
        self.session = session or self.session

    def scope(self, action):
        values = {**DEFAULTS, **action['arguments']}
        values.pop('cursor', None)
        values['evidence_ref'] = self.canonical(values['evidence_ref'])
        return json.dumps(values, sort_keys=True, ensure_ascii=False)

    def prepare(self, action):
        """Return a copy, a pending original continuation, or None for coverage."""
        if action['tool'] != 'read_evidence':
            return copy.deepcopy(action)
        scope = self.scope(action)
        state = self.scopes.get(scope)
        cursor = action['arguments'].get('cursor')
        # Unknown cursors are not assumed to overlap a completed range.
        if state and (cursor is None or cursor in state['accepted_cursors']):
            if state['complete']:
                self.events.append({'kind': 'skip_completed_scope', 'scope': scope})
                return None
            if state['continuation']:
                self.events.append({'kind': 'resume_partial_scope', 'scope': scope})
                return copy.deepcopy(state['continuation'])
        return copy.deepcopy(action)

    def observe(self, action, response):
        """Commit coverage only AFTER the corresponding read actually succeeds.

        Do not call this for search/inspect results. The response shape is also
        checked so a preview or a bare raw_text cannot complete a container.
        Exceptions/timeouts have no response and must not call observe.
        """
        if action['tool'] != 'read_evidence' or response.get('status') != 'ok':
            return False
        args = action['arguments']
        if response.get('corpus_ref', args['corpus_ref']) != args['corpus_ref']:
            raise ValueError('read response corpus differs from requested scope')
        data = response.get('data')
        if not isinstance(data, dict):
            return False
        self.learn(response, requested_ref=args['evidence_ref'])
        exact = data.get('evidence_ref')
        if not exact or exact.startswith('[H') or exact != self.canonical(args['evidence_ref']):
            self.events.append({'kind': 'identity_not_established'})
            return False
        view = args.get('view', 'physical')
        container = (view == 'physical' and isinstance(data.get('context'), list)
                     and isinstance(data.get('metadata'), dict)
                     and isinstance(data.get('physical_container'), str)
                     and data.get('delivered_count') == len(data['context']))
        dialogue = (view == 'dialogue' and data.get('view') == 'dialogue'
                    and isinstance(data.get('nodes'), list)
                    and data.get('delivered_count') == len(data['nodes']))
        window = (view == 'physical' and isinstance(data.get('raw_text'), str)
                  and isinstance(data.get('context'), list)
                  and isinstance(data.get('container_ref'), str)
                  and isinstance(data.get('provenance'), dict)
                  and data.get('context_truncated') is False)
        next_cursor = data.get('next_cursor')
        # Legacy dialogue responses mix frontier and source uncertainty in
        # partial. They may be continued, but an uncertain chain is never
        # promoted to complete merely because its final page has no cursor.
        legacy_graph = dialogue and isinstance(data.get('reachable_local_graph_exhausted'), bool)
        eof = (data.get('scope_complete') is True or (window and 'scope_complete' not in data)
               or (legacy_graph and data['reachable_local_graph_exhausted']))
        paginated_partial = dialogue and not eof and isinstance(next_cursor, str) and bool(next_cursor)
        incomplete = ((data.get('partial') and not paginated_partial)
                      or data.get('context_truncated') or data.get('truncated')
                      or data.get('page_info', {}).get('truncated'))
        if (not (container or dialogue or window) or incomplete
                or (eof and next_cursor is not None)
                or (not eof and not isinstance(next_cursor, str))
                or next_cursor == ''):
            self.events.append({'kind': 'incomplete_or_unsupported_read', 'exact_ref': exact})
            return False
        key = self.scope(action)
        cursor = args.get('cursor')
        state = self.scopes.get(key)
        fresh = cursor is None and state is None
        if fresh:
            state = self.scopes[key] = {'complete': False, 'continuation': None,
                                       'accepted_cursors': [], 'ranges': [], 'uncertain': False}
        expected = (state and state['continuation'] and
                    state['continuation']['arguments']['cursor'])
        if not state or (cursor is None and not fresh) or (cursor is not None and cursor != expected) or state['complete']:
            self.events.append({'kind': 'out_of_chain_read', 'scope': key, 'cursor': cursor})
            return False
        rows = []
        def collect(value):
            if isinstance(value, dict):
                if isinstance(value.get('raw_text'), str) and value.get('evidence_ref'):
                    rows.append({k: value[k] for k in ('evidence_ref', 'ordinal', 'pointer',
                                 'locale', 'text_range', 'start', 'end') if k in value})
                for name, child in value.items():
                    if name not in ('reading_view', 'source_context'):
                        collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
        collect(data)
        state['accepted_cursors'].append(cursor)
        state['ranges'].append({'cursor': cursor, 'physical_rows': rows})
        state['uncertain'] |= bool(data.get('partial'))
        state['complete'] = bool(eof and not state['uncertain'])
        state['continuation'] = None if eof else {
            'tool': 'read_evidence', 'arguments': {**args, 'cursor': next_cursor}}
        return True
