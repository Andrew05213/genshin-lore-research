"""Local, write-once delivery of selected read/inspect evidence; no claim inference."""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
import re


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n'


def _fence(text, language=''):
    fence = '`' * max(3, 1 + max((len(m[0]) for m in re.finditer(r'`+', text)), default=0))
    return f'{fence}{language}\n{text}' + ('' if text.endswith('\n') else '\n') + fence + '\n'


def _write_once(path, raw):
    try:
        with path.open('xb') as stream:
            stream.write(raw)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != raw:
            raise ValueError('existing source card differs; never overwritten: ' + str(path))


def _card_value(record, inspection, corpus_ref):
    """Inputs come from a server-owned read mapping and actual formal inspection."""
    if (not record.get('material_read') or record.get('operation') not in ('read', 'query')
            or record.get('quoted_text') is None or record['source_result'].get('locator_only')):
        raise ValueError('save_source requires an actually read text handle, not a search/inspect locator')
    if inspection.get('evidence_ref') != record['evidence_ref']:
        raise ValueError('source card inspection/reference mismatch')
    text = record['quoted_text']
    if hashlib.sha256(text.encode()).hexdigest() != record['quoted_text_sha256']:
        raise ValueError('source card quoted text SHA mismatch')
    provenance = inspection.get('text_provenance')
    if not isinstance(provenance, dict):
        raise ValueError('source card requires text-level inspected provenance')
    # Keep the original immutable read mapping (including its original flags),
    # and record the subsequent inspection separately. No status is upgraded.
    value = {'contract': 'research-source-card-v1', 'corpus_ref': corpus_ref,
             'evidence_ref': record['evidence_ref'], 'snapshot': provenance.get('snapshot'),
             'quoted_text': text, 'quoted_text_sha256': record['quoted_text_sha256'],
             'observation_index': record['observation_index'], 'range': record['range'],
             'read_completed': True, 'inspect_completed': True,
             'read_record': record, 'inspection': inspection,
             'locale': record['source_result'].get('locale', record.get('reading_context', {}).get('locale')),
             'claim_support': 'NOT_AUTOMATICALLY_CHECKED; no ownership, speaker or order inference'}
    return value


def _reading_body(value):
    record = value['read_record']
    text = value['quoted_text']
    context = record.get('reading_context', {})
    source = record['source_result']
    display = source.get('source_context') or {}
    title = source.get('source_title') or context.get('display_title') or display.get('display_title')
    subtitle = context.get('display_subtitle') or display.get('display_subtitle')
    titles = context.get('titles', source.get('source_title_candidates', []))
    if not title and titles:
        title = ' / '.join(t['raw_text'] for t in titles if isinstance(t.get('raw_text'), str))
    if subtitle and subtitle != title:
        title = (title + ' · ' + subtitle) if title else subtitle
    speaker = context.get('speaker', {})
    label = source.get('source_speaker_label') or source.get('speaker_label') or speaker.get('display_name')
    # A fence preserves the displayed source, including Markdown/backticks in it.
    # The JSON stores exact original characters and SHA; the display adds a final
    # newline only when required by Markdown, never a different source hash.
    body = '## ' + html.escape((title or '未命名片段').replace('\n', ' ')) + '\n\n'
    if label: body += html.escape(label) + '：\n\n'
    return body + _fence(text) + '\n'


def _save(directory, value, entries):
    raw = _json(value).encode()
    identity = hashlib.sha256(raw).hexdigest()
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    metadata = directory / (identity + '.json')
    # A new display filename leaves previously delivered cards untouched.
    markdown = directory / (identity + '-reading-v2.md')
    body = '# 阅读摘录\n\n' + ''.join(_reading_body(entry) for entry in entries)
    _write_once(metadata, raw)
    _write_once(markdown, body.encode())
    return {'status': 'SAVED', 'card_sha256': identity,
            'markdown_path': str(markdown), 'metadata_path': str(metadata),
            'markdown_link': f'[来源卡](<{markdown.as_posix()}>)',
            'read_completed': True, 'inspect_completed': True,
            'scope': 'selected read view only; native answer is not intercepted or corrected'}


def save_card(directory, record, inspection, corpus_ref):
    """Single-selection metadata stays compatible; human display is body-only."""
    value = _card_value(record, inspection, corpus_ref)
    return _save(directory, value, [value])


def save_collection(directory, selections, corpus_ref):
    """Save explicit selections in caller order, never infer narrative continuity."""
    entries = [_card_value(record, inspection, corpus_ref) for record, inspection in selections]
    if not 1 <= len(entries) <= 32:
        raise ValueError('source collection requires 1..32 selected read handles')
    value = {'contract': 'research-source-card-collection-v1', 'corpus_ref': corpus_ref,
             'entries': entries, 'ordering': 'caller selection order; not narrative chronology',
             'claim_support': 'NOT_AUTOMATICALLY_CHECKED'}
    return _save(directory, value, entries)
