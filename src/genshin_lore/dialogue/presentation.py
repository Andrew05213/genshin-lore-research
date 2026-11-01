"""Reading views only: preserve evidence identities, never infer identity/claims."""
from __future__ import annotations

import copy
import re
from urllib.parse import quote

READING_PAGE_BYTES = 160_000  # leaves room for scoped citation metadata under MCP's 256 KiB cap
# Existing Character decoder fields, not inferred roles for unknown fields.
CHARACTER_BODY_FIELDS = {'descTextMapHash', 'avatarDetailTextMapHash', 'storyContextTextMapHash',
                         'storyContext2TextMapHash', 'voiceFileTextTextMapHash'}
CHARACTER_TITLE_FIELDS = {'storyTitleTextMapHash', 'storyTitle2TextMapHash', 'voiceTitleTextMapHash'}


def check_detail(detail):
    if detail not in ('reading', 'full'):
        raise ValueError('detail must be reading or full; inspect_evidence always returns full provenance')


def reading_card(card):
    """Remove repeated provenance from the view; exact refs/proof states remain."""
    value = copy.deepcopy(card)
    def visit(node):
        if isinstance(node, list):
            for item in node: visit(item)
        elif isinstance(node, dict):
            link = source_link(node)
            if link: node['source_link'] = link
            provenance = node.pop('provenance', None)
            if provenance:
                node['source_locator'] = {k: provenance[k] for k in ('source_file', 'source_pointer') if k in provenance}
            for item in node.values(): visit(item)
    visit(value)
    return value


def page_groups(nodes):
    groups = {}
    for i, node in enumerate(nodes):
        for j, text in enumerate(node.get('texts', [])):
            key = text.get('locale'), text['raw_text']
            group = groups.setdefault(key, {'text': text['raw_text'], 'locale': text.get('locale'),
                'occurrences': [], 'display_group_only': True, 'logical_identity_proven': False})
            group['occurrences'].append({'node_index': i, 'text_index': j})
    return list(groups.values())


def page_item(card, titles):
    """A body-first page: source titles once, surrounding nodes as exact locators."""
    item = reading_card(card)
    catalog = list(titles)
    context = item.get('source_context', {})
    speaker = item.get('speaker', {}).get('display_name')
    if speaker:
        for text in item.get('texts', []):
            text['source_speaker_label'] = speaker
            if text.get('citation_label'): text['citation_label'] += ' · ' + speaker
    indexes = []
    for title in context.pop('titles', []):
        index = next((i for i, t in enumerate(catalog) if t['evidence_ref'] == title['evidence_ref']), None)
        if index is None:
            index = len(catalog); catalog.append(title)
        indexes.append(index)
    if indexes: context['title_indexes'] = indexes
    for direction in ('before', 'after'):
        if direction in context:
            context[direction] = [{'node': n['node'], 'texts': [{'evidence_ref': t['evidence_ref']} for t in n.get('texts', [])],
                'speaker_label': n.get('speaker', {}).get('display_name'),
                'speaker_status': n.get('speaker', {}).get('status')} for n in context[direction]]
    context['neighbour_delivery'] = 'locators only; read a returned text ref with detail=full for surrounding bodies'
    # The text refs still encode their original container; inspect returns that full identity.
    item.pop('container_ref', None)
    if item.get('matched_speaker_ref') in [n['evidence_ref'] for n in item.get('speaker', {}).get('names', [])]:
        item.pop('matched_speaker_ref'); item['source_name_match_in_speaker_names'] = True
    return item, catalog


def source_link(item):
    """A real upstream file link, explicitly not a permalink to this observation."""
    provenance = item.get('provenance', item.get('text_provenance', {}))
    if not isinstance(provenance, dict): return None
    snapshot = provenance.get('snapshot', {})
    identity = snapshot.get('id', '') if isinstance(snapshot, dict) else snapshot
    if not isinstance(identity, str): return None
    commit = identity.rsplit(':', 1)[-1]
    path = provenance.get('source_file')
    if identity.startswith('anime-game-data:') and re.fullmatch('[0-9a-f]{40}', commit) and isinstance(path, str) and path:
        if not path.startswith('/') and '..' not in path.split('/'):
            return {'url': 'https://github.com/DimbreathBot/AnimeGameData/blob/' + commit + '/' + quote(path, safe='/'),
                    'scope': 'source_file_only; use inspect_evidence for exact observation/range'}
    return None


def text_groups(items):
    """Group display text, retaining every physical occurrence and proof state."""
    groups = {}
    for item in items:
        body = item.get('raw_text', item.get('snippet'))
        if not isinstance(body, str): continue
        key = (item.get('locale'), body)
        group = groups.setdefault(key, {'text': body, 'locale': item.get('locale'),
            'occurrences': [], 'display_group_only': True, 'logical_identity_proven': False})
        group['occurrences'].append({k:item[k] for k in (
            'evidence_ref', 'citation_handle', 'citation_number', 'citation_label',
            'source_locator', 'source_path', 'physical_pointer', 'text_proof', 'match_span') if k in item})
    return list(groups.values())


def character_reading(service, query, locale):
    """At most one literal family lookup and two already-supported navigation hops."""
    families = sorted({c['family'] for c in service.components.values() if c.get('adapter') == 'character_text'})
    if not families: return [], []
    anchors = service.search(service.corpus_ref, query, locale, families, limit=3)['items']
    exact = [a for a in anchors if a.get('snippet') == query and a.get('owner_field') == 'nameTextMapHash']
    if not exact: return [], []
    anchor = exact[0]['evidence_ref']
    action = {'tool': 'follow_evidence', 'arguments': {'corpus_ref': service.corpus_ref,
        'evidence_ref': anchor}, 'reason': 'first bounded exact-name Character candidate; not unique identity or logical ownership',
        'anchor_candidates': [{'evidence_ref': a['evidence_ref']} for a in exact],
        'anchor_discovery_complete': False}
    navigation = service.follow(service.corpus_ref, anchor, relation_kind='character_story_voice_candidate', limit=2)
    bodies, seen = [], set()
    for edge in navigation['items']:
        for candidate in edge.get('candidates', []):
            ref = candidate.get('target_evidence_ref')
            if not ref or ref in seen or len(seen) >= 2: continue
            seen.add(ref)
            material = service.read(service.corpus_ref, ref, limit=50)
            titles = [t for t in material.get('context', []) if t.get('locale') == locale and t.get('owner_field') in CHARACTER_TITLE_FIELDS]
            delivered = 0
            for text in material.get('context', []):
                if text.get('locale') == locale and text.get('owner_field') in CHARACTER_BODY_FIELDS and delivered < 2:
                    bodies.append({**text, 'discovery_basis': 'source-authored Character navigation; physical_candidate',
                        'source_title_candidates': titles,
                        'relation_status': edge['status'], 'logical_identity_proven': False,
                        'requires_read_and_inspect': True})
                    delivered += 1
    return bodies, [action]


def search_reading(service, result, query, locale, *, filters=False):
    """An additive bounded reading panel; literal items and cursor stay untouched."""
    from .context import DialogueContext
    helper = DialogueContext(service)
    names, bodies, actions = [], [], []
    seen = set()
    body_indexes = []
    for index, item in enumerate(result.get('items', [])):
        is_name = item.get('owner_field') in {'nameTextMapHash', 'avatarName', 'name', 'Name'}
        if not is_name:
            body_indexes.append(index)
            continue
        names.append({'item_index': index, 'name': item.get('snippet'), 'family': item.get('family')})
        ref = item['evidence_ref']
        action = {'tool': 'follow_evidence', 'arguments': {'corpus_ref': service.corpus_ref, 'evidence_ref': ref},
                  'reason': 'discover only returned source-bound story/voice/dialogue candidates'}
        if item.get('family') == 'npc_archive':
            action = {'tool': 'read_evidence', 'arguments': {'corpus_ref': service.corpus_ref,
                'evidence_ref': ref, 'view': 'dialogue', 'locale': locale or item.get('locale') or 'CHS', 'limit': 20},
                'reason': 'source-labelled speech candidates; name equality does not prove one actor'}
            # Same literal name seeds the existing Codex reader once, not once per homonym row.
            name = item.get('snippet')
            if name == query and name not in seen and not filters:
                seen.add(name)
                page = helper.read(service.corpus_ref, ref, limit=3, locale=action['arguments']['locale'], detail='reading')
                for node in page['nodes']:
                    for text in node.get('texts', []):
                        bodies.append({**text, 'source_title': node.get('source_context', {}).get('display_title'),
                            'speaker_label': node.get('speaker', {}).get('display_name'),
                            'discovery_basis': 'source-labelled name candidate; not unique NPC ownership',
                            'requires_read_and_inspect': True})
        actions.append(action)
    if names and not filters:
        character_bodies, character_actions = character_reading(service, query, locale or 'CHS')
        bodies = character_bodies + bodies
        actions = character_actions + actions
    return {'body_previews': bodies, 'body_item_indexes': body_indexes, 'name_matches': names, 'text_groups': text_groups(bodies),
            'next_actions': actions[:3], 'preview_scope': 'current search page; unfiltered exact name: at most three speech nodes plus two Character navigation targets. Not exhaustive; follow scopes differ from search hits.',
            'partial': True, 'logical_identity_proven': False,
            'original_items_and_cursor_unchanged': True}
