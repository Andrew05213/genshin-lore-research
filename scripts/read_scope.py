"""Read one explicit physical MCP scope to EOF; stop without exploring other refs.

This is an opt-in caller example, not automatic server deduplication or an Agent.
Use an exact ref previously returned by this corpus, never an expired handle.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.read_scope_state import ReadState


def payload(result):
    if result.get('isError'):
        raise ValueError('MCP tool error; see retained response')
    if result.get('structuredContent') is not None:
        return result['structuredContent']
    return json.loads(next(x['text'] for x in result['content'] if x['type'] == 'text'))


class StdioReader:
    """Sequential stdio transport with a fresh coverage state per connection."""

    def __init__(self, command, output, *, cwd=ROOT, env=None, timeout=180):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=False)
        self.state = ReadState()
        self.calls = []
        self.serial = 0
        self.timeout = timeout
        self.broken = False
        self.pending_checks = {}
        self.stderr = (self.output/'stderr.log').open('x', encoding='utf-8')
        self.wire = (self.output/'wire.jsonl').open('x', encoding='utf-8')
        self.events = queue.Queue()
        self.wire_lock = threading.Lock()
        self.started = time.perf_counter()
        self.proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self.stderr,
                                     text=True, encoding='utf-8', bufsize=1)
        def receive():
            try:
                for line in self.proc.stdout:
                    try:
                        message = json.loads(line)
                    except ValueError:
                        message = {'invalid_json_line': line}
                    self.record('response', message, raw_line=line)
                    self.events.put(message)
            finally:
                self.events.put(None)
        self.receiver = threading.Thread(target=receive, daemon=True)
        self.receiver.start()

    def record(self, direction, message, *, raw_line=None):
        with self.wire_lock:
            self.wire.write(json.dumps({'direction': direction, 'message': message,
                'raw_line': raw_line, 'elapsed': time.perf_counter()-self.started},
                ensure_ascii=False)+'\n')
            self.wire.flush()

    def rpc(self, method, params):
        if self.broken:
            raise RuntimeError('connection closed or timed out; create a fresh reader')
        self.serial += 1
        message = {'jsonrpc': '2.0', 'id': self.serial, 'method': method, 'params': params}
        call = {'method': method, 'params': params}
        self.calls.append(call)
        self.record('request', message)
        start = time.perf_counter()
        try:
            self.proc.stdin.write(json.dumps(message, ensure_ascii=False)+'\n')
            self.proc.stdin.flush()
            while True:
                remaining = self.timeout-(time.perf_counter()-start)
                if remaining <= 0:
                    raise TimeoutError('MCP response timeout')
                try:
                    line = self.events.get(timeout=remaining)
                except queue.Empty as exc:
                    raise TimeoutError('MCP response timeout') from exc
                if line is None:
                    raise RuntimeError('MCP exited before response')
                response = line
                if 'invalid_json_line' in response:
                    raise ValueError('invalid JSON from MCP; see retained line')
                if response.get('id') == self.serial:
                    call['response'] = response
                    if 'error' in response:
                        raise ValueError('JSON-RPC error; see retained response')
                    return response['result']
        except BaseException as exc:
            call['error_type'] = type(exc).__name__
            # A late response must not be mistaken for a retried read.
            self.broken = True
            self.state.clear()
            self.pending_checks.clear()
            raise
        finally:
            call['seconds'] = time.perf_counter()-start

    def initialize(self):
        self.rpc('initialize', {'protocolVersion': '2025-11-25', 'capabilities': {},
            'clientInfo': {'name': 'explicit-read-scope', 'version': '1'}})
        notice = {'jsonrpc': '2.0', 'method': 'notifications/initialized'}
        self.record('request', notice)
        self.proc.stdin.write(json.dumps(notice)+'\n')
        self.proc.stdin.flush()
        self.rpc('tools/list', {})

    def call(self, tool, arguments):
        response = payload(self.rpc('tools/call', {'name': tool, 'arguments': arguments}))
        self.calls[-1]['payload'] = response
        if tool != 'read_evidence':
            self.state.learn(response, requested_ref=arguments.get('evidence_ref')
                             if tool == 'inspect_evidence' else None)
        return response

    def read(self, arguments):
        action = self.state.prepare({'tool': 'read_evidence', 'arguments': arguments})
        if action is None:
            return None  # caller decision; never a fabricated server response
        response = self.call('read_evidence', action['arguments'])
        if self.state.observe(action, response):
            # Every accepted read belongs to the qualification ledger, even
            # when a caller reads the first page before invoking read_one_scope.
            key = self.state.scope(action)
            pending = self.pending_checks.setdefault(key, [])
            for handle in text_handles(response['data']):
                if handle not in pending:
                    pending.append(handle)
        return response

    @property
    def business_calls(self):
        return sum(x['method'] == 'tools/call' for x in self.calls)

    def close(self):
        self.broken = True
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        finally:
            self.receiver.join(timeout=5)
            with (self.output/'calls.json').open('x', encoding='utf-8') as f:
                json.dump({'calls': self.calls, 'events': self.state.events,
                           'scopes': self.state.scopes, 'exit_code': self.proc.returncode},
                          f, ensure_ascii=False, indent=2)
            self.state.clear()
            self.pending_checks.clear()
            self.stderr.close()
            self.wire.close()


def text_handles(value):
    """Only actual read bodies, not preview/locator-only entries."""
    if isinstance(value, dict):
        if isinstance(value.get('raw_text'), str) and not value.get('locator_only'):
            handle = value.get('citation_handle')
            if handle:
                yield handle
        for key, child in value.items():
            if key not in ('reading_view', 'source_context', 'source_titles', 'speaker'):
                yield from text_handles(child)
    elif isinstance(value, list):
        for child in value:
            yield from text_handles(child)


def read_one_scope(client, arguments, *, max_calls=20):
    """Read and inspect this one scope, stopping at proven local EOF or budget."""
    root = {'tool': 'read_evidence', 'arguments': arguments}
    pages = 0
    key = client.state.scope(root)
    while True:
        # Read coverage is separate from source qualification. A failed inspect
        # remains pending even when the last read already reached local EOF.
        pending = client.pending_checks.get(key, [])
        while pending:
            if client.business_calls >= max_calls:
                return {'complete': False, 'stop_reason': 'call_budget', 'pages': pages}
            inspected = client.call('inspect_evidence', {
                'corpus_ref': arguments['corpus_ref'], 'evidence_refs': pending[:32],
                'save_source': True, 'detail': 'full'})
            if (inspected.get('status') != 'ok' or inspected['data'].get('partial')
                    or inspected['data'].get('source_card', {}).get('status') != 'SAVED'):
                return {'complete': False, 'stop_reason': 'inspection_incomplete', 'pages': pages}
            del pending[:32]
        client.pending_checks.pop(key, None)
        state = client.state.scopes.get(key)
        if state and state['complete'] and client.state.prepare(root) is None:
            return {'complete': True, 'stop_reason': 'scope_eof', 'pages': pages}
        if client.business_calls + 2 > max_calls:
            return {'complete': False, 'stop_reason': 'call_budget', 'pages': pages}
        response = client.read(arguments)
        if response is None:
            return {'complete': True, 'stop_reason': 'already_covered', 'pages': pages}
        if response.get('status') != 'ok':
            return {'complete': False, 'stop_reason': 'read_rejected', 'pages': pages}
        pages += 1
        key = client.state.scope(root)
        state = client.state.scopes.get(key)
        if not state:
            return {'complete': False, 'stop_reason': 'unproven_continuation', 'pages': pages}
        if not state['complete'] and not state['continuation']:
            return {'complete': False, 'stop_reason': 'unproven_continuation', 'pages': pages}



def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--evidence-ref', required=True, help='exact returned ref; not a scoped handle')
    parser.add_argument('--output-dir', type=Path, required=True, help='new private output directory')
    parser.add_argument('--locale', default='CHS', choices=['CHS', 'EN', 'JP'])
    parser.add_argument('--limit', type=int, default=20, choices=range(1, 33), metavar='1..32')
    parser.add_argument('--max-calls', type=int, default=20)
    args = parser.parse_args(argv)
    if args.evidence_ref.startswith('[H'):
        parser.error('handles cannot cross connections; use a returned exact ref')
    if args.max_calls < 3:
        parser.error('--max-calls must allow describe/read/inspect')
    # All I/O and process creation begins after argument parsing, including help.
    output = args.output_dir.resolve()
    command = [sys.executable, str(ROOT/'scripts/serve_dialogue.py'), '--config',
               str(args.config.resolve()), '--source-cards', str(output/'source-cards')]
    env = {**os.environ, 'PYTHONPATH': str(ROOT/'src'), 'PYTHONUTF8': '1',
           'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}
    client = StdioReader(command, output, env=env)
    try:
        client.initialize()
        described = client.call('describe_corpus', {})
        if described.get('status') != 'ok':
            raise ValueError('corpus discovery rejected')
        corpus = described['corpus_ref']
        result = read_one_scope(client, {'corpus_ref': corpus,
            'evidence_ref': args.evidence_ref, 'locale': args.locale,
            'limit': args.limit, 'detail': 'full', 'view': 'physical'},
            max_calls=args.max_calls)
        result['business_calls'] = client.business_calls
        with (output/'summary.json').open('x', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['complete'] else 1
    finally:
        client.close()


if __name__ == '__main__':
    raise SystemExit(main())
