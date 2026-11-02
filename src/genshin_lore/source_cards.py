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


def save_card(directory, record, inspection, corpus_ref):
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
    raw = _json(value).encode()
    identity = hashlib.sha256(raw).hexdigest()
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    metadata = directory / (identity + '.json')
    markdown = directory / (identity + '.md')
    context = record.get('reading_context', {})
    source = record['source_result']
    title = source.get('citation_label') or source.get('source_title') or inspection.get('citation_label')
    titles = context.get('titles', source.get('source_title_candidates', []))
    if not title and titles:
        title = ' / '.join(t['raw_text'] for t in titles if isinstance(t.get('raw_text'), str))
    speaker = context.get('speaker', {})
    label = source.get('source_speaker_label') or source.get('speaker_label') or speaker.get('display_name')
    # A fence preserves the displayed source, including Markdown/backticks in it.
    # The JSON stores exact original characters and SHA; the display adds a final
    # newline only when required by Markdown, never a different source hash.
    body = '# 来源卡\n\n'
    body += '**来源标题标注：** ' + html.escape(title or '未解析；以下物理坐标为准') + '\n\n'
    body += '**来源角色标注：** ' + html.escape(label or '未解析') + '\n\n'
    body += '**语言：** ' + html.escape(value['locale'] or '未解析') + '\n\n'
    body += '正文已读取；来源已 inspect。标题/角色标注及候选限制见下方，不由卡片确认唯一归属或剧情顺序；引用不自动证明紧邻陈述。\n\n'
    body += '## 实际读取的原文\n\n' + _fence(text) + '\n'
    body += 'JSON 保留原始字符与 SHA；代码块仅在缺少末尾换行时补显示换行。\n\n'
    body += '## 位置、范围与限制\n\n' + _fence(_json({
        'snapshot': value['snapshot'], 'corpus_ref': corpus_ref,
        'observation_index': record['observation_index'], 'read_range': record['range'],
        'text_provenance': provenance, 'container_provenance': inspection.get('container_provenance'),
        'reading_context': context,
        'read_source_state': {k: source[k] for k in ('locale', 'owner_field', 'text_proof', 'proof_status', 'limitations') if k in source},
        'inspect_source_state': {k: inspection[k] for k in ('text_proof', 'terminal_unresolved', 'relation_resolution', 'source_context') if k in inspection},
        'quoted_text_sha256': value['quoted_text_sha256']}), 'json') + '\n'
    body += 'EOF/范围结束只属于当前文件、记录或读取范围，不代表全剧情或全 corpus。\n\n'
    body += '## 精确引用\n\n' + _fence(record['evidence_ref']) + '\n'
    body += '重启后以此 exact ref 重新 read/inspect；临时 handle 不是永久身份。\n\n'
    body += f'[完整机器记录]({metadata.name})\n'
    _write_once(metadata, raw)
    _write_once(markdown, body.encode())
    return {'status': 'SAVED', 'card_sha256': identity,
            'markdown_path': str(markdown), 'metadata_path': str(metadata),
            'markdown_link': f'[来源卡](<{markdown.as_posix()}>)',
            'read_completed': True, 'inspect_completed': True,
            'scope': 'selected read view only; native answer is not intercepted or corrected'}
