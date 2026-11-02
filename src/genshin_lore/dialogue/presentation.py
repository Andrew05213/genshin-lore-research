"""Reading views only: preserve evidence identities, never infer identity/claims."""
from __future__ import annotations

import copy
import re
from urllib.parse import quote

READING_PAGE_BYTES = 160_000  # leaves room for scoped citation metadata under MCP's 256 KiB cap


class DialoguePageBudgetExceeded(ValueError):
    """A single qualified node cannot fit this bounded reading view."""
    def __init__(self, message, text_refs=()):
        super().__init__(message)
        self.text_refs = tuple(text_refs)


# Existing Character decoder fields, not inferred roles for unknown fields.
CHARACTER_BODY_FIELDS = {'descTextMapHash', 'avatarDetailTextMapHash', 'storyContextTextMapHash',
                         'storyContext2TextMapHash', 'voiceFileTextTextMapHash'}
CHARACTER_TITLE_FIELDS = {'storyTitleTextMapHash', 'storyTitle2TextMapHash', 'voiceTitleTextMapHash'}
# Display hints for fields already declared by the source adapters/readers.
# Unknown fields stay unclassified; these labels never upgrade source proof.
NAME_FIELDS = {'nameTextMapHash', 'avatarName', 'name', 'Name', 'SpeakerKnown'}
TITLE_FIELDS = CHARACTER_TITLE_FIELDS | {'MainQuestTitle', 'SubQuestTitle', 'ChapterTitle',
    'titleTextMapHash', 'title', 'Title', 'storyTitleLockedTextMapHash', 'voiceTitleLockedTextMapHash'}
BODY_FIELDS = CHARACTER_BODY_FIELDS | {'body', 'talkContentTextMapHash', 'contentTextMapHash',
    'stepDescTextMapHash', 'MainQuestDesp', 'reviewTextMapHash', 'markDescTextMapHash',
    'voiceDescTextMapHash', 'markTipContentTextMapHash', 'cgDesTextMapHash', 'newsTextTextMapHash',
    'talkContent/0', 'talkContent/1'}


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
            group = groups.setdefault(key, {'representative': {'node_index': i, 'text_index': j}, 'locale': text.get('locale'),
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
            context[direction] = [{'node': n['node'], 'texts': [{'evidence_ref': t['evidence_ref'], 'locator_only': True} for t in n.get('texts', [])],
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
    """Index-only display groups; identities and proof remain on each preview."""
    groups = {}
    for index, item in enumerate(items):
        body = item.get('raw_text', item.get('snippet'))
        if not isinstance(body, str): continue
        key = (item.get('locale'), body)
        group = groups.setdefault(key, {'representative': {'preview_index': index}, 'locale': item.get('locale'),
            'occurrences': [], 'display_group_only': True, 'logical_identity_proven': False})
        group['occurrences'].append({'preview_index': index})
    return list(groups.values())


def trim_search_reading(data):
    """Reduce optional display data only; never change hits, refs or cursor."""
    panel = data.get('reading_view')
    if panel is None: return False
    if panel.get('body_previews'):
        panel['body_previews'].pop()
        panel['text_groups'] = text_groups(panel['body_previews'])
        panel['groups'] = search_groups(panel)
        panel['omitted_preview_count'] = panel.get('omitted_preview_count', 0) + 1
        panel['omission_reason'] = 'response_byte_limit'
    elif 'body_previews' in panel:
        data['reading_view'] = {'omission_reason': 'response_byte_limit', 'partial': True,
                                'original_items_and_cursor_unchanged': True}
    else:
        del data['reading_view']
    return True


def corpus_overview(result):
    """Compact discovery only; the underlying full description stays available."""
    if not isinstance(result.get('components'), list) or 'search_semantics' not in result:
        return result
    value = {k: copy.deepcopy(v) for k, v in result.items()
             if k not in ('components', 'discovery_domains', 'frozen_evidence_input_bundle')}
    components = result['components']
    value['component_summary'] = {
        'count': len(components),
        'unavailable': [c['artifact_source_key'] for c in components if not c.get('available', True)],
        'partial_projection_count': sum(not c.get('overall_projection_complete', False) for c in components),
        'partial_projection_sources': [c['artifact_source_key'] for c in components
                                       if not c.get('overall_projection_complete', False)],
        'counts_are_not_game_coverage': True}
    semantics = value['search_semantics']
    if 'semantic_coverage' in semantics:
        coverage = semantics.pop('semantic_coverage')
        semantics['semantic_overlay_present'] = coverage is not None
        semantics['semantic_scope'] = 'bounded discovery; exact scope available with detail=full'
    domains = result.get('discovery_domains')
    if isinstance(domains, dict):
        value['discovery_domain_names'] = sorted(domains.get('domains', {}))
    value['detail'] = 'reading'
    value['details_action'] = {'tool': 'describe_corpus', 'arguments': {
        'corpus_ref': result['corpus_ref'], 'detail': 'full'}}
    return value


def search_groups(panel):
    """Pointers into unchanged hits/previews, never an entity or speaker join."""
    groups = {'character_material_candidates': [], 'source_labelled_speech_candidates': [],
              'other_body_previews': [],
              'keyword_body_matches': [{'item_index': i} for i in panel['body_item_indexes']],
              'name_matches': [{'item_index': n['item_index']} for n in panel['name_matches']],
              'unclassified_matches': [{'item_index': n['item_index']} for n in panel['other_matches']]}
    by_title = {}
    for index, body in enumerate(panel['body_previews']):
        basis = body.get('discovery_basis', '')
        kind = ('character_material_candidates' if 'Character' in basis else
                'source_labelled_speech_candidates' if 'source-labelled' in basis else 'other_body_previews')
        pointer = {'preview_index': index}
        groups[kind].append(pointer)
        titles = ([body['source_title']] if body.get('source_title') else
                  [t['raw_text'] for t in body.get('source_title_candidates', []) if t.get('raw_text')])
        for title in titles:
            by_title.setdefault(title, []).append(pointer)
    for title in panel['title_matches']:
        by_title.setdefault(title['title'], []).append({'item_index': title['item_index']})
    return {'categories': groups,
            'by_source_title': [{'title': title, 'matches': pointers} for title, pointers in by_title.items()],
            'scope': 'current page only; keyword mentions do not establish a speaker',
            'display_group_only': True, 'logical_identity_proven': False}


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
    from .codex import BODY_LABELS
    helper = DialogueContext(service)
    names, bodies, actions = [], [], []
    seen = set()
    preview_omission = None
    body_indexes, titles, other = [], [], []
    for index, item in enumerate(result.get('items', [])):
        field = item.get('owner_field')
        if field in TITLE_FIELDS:
            titles.append({'item_index': index, 'title': item.get('snippet'), 'family': item.get('family')})
            actions.append({'tool': 'read_evidence', 'arguments': {'corpus_ref': service.corpus_ref,
                'evidence_ref': item.get('container_ref') or item['evidence_ref'], 'limit': 20},
                'reason': 'read the returned physical container; a title match is not body evidence'})
            continue
        if field in BODY_FIELDS or field in BODY_LABELS:
            body_indexes.append(index)
            continue
        if field not in NAME_FIELDS:
            other.append({'item_index': index, 'owner_field': field, 'field_role': 'unclassified'})
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
                try:
                    page = helper.read(service.corpus_ref, ref, limit=3, locale=action['arguments']['locale'], detail='reading')
                except DialoguePageBudgetExceeded as exc:
                    # The name search is valid. Only this optional view failed;
                    # never hide integrity/scope failures under a broad ValueError.
                    preview_omission = 'preview_read_byte_limit'
                    page = {'nodes': []}
                    if exc.text_refs:
                        action = {'tool': 'read_evidence', 'arguments': {'corpus_ref': service.corpus_ref,
                            'evidence_ref': exc.text_refs[0], 'view': 'physical'},
                            'reason': 'qualified body locator only; preview exceeded page budget, read body then inspect'}
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
    panel = {'body_previews': bodies, 'body_item_indexes': body_indexes, 'name_matches': names,
            'title_matches': titles, 'other_matches': other, 'text_groups': text_groups(bodies),
            **({'omission_reason': preview_omission} if preview_omission else {}),
            'next_actions': actions[:3], 'preview_scope': 'current search page; unfiltered exact name: at most three speech nodes plus two Character navigation targets. Not exhaustive; follow scopes differ from search hits.',
            'partial': True, 'logical_identity_proven': False,
            'original_items_and_cursor_unchanged': True}
    panel['groups'] = search_groups(panel)
    return panel
