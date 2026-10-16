"""Bounded reading views over declared dialogue adapters, not a story graph.

The existing index and qualified artifacts are the only authorities. A view
keeps each physical occurrence separate and never invents actors or chronology.
"""
from __future__ import annotations

import hashlib
import json

from ..research_refs import decode_ref

MAX_PAGE_BYTES = 80_000
MAX_VISITED = 128


class DialogueReadAdapter:
    """Opt-in maintenance view on an already qualified release runtime.

    The underlying runtime keeps its own startup guards and exact refs. Its
    code is not patched; this adapter delegates all unchanged operations.
    """
    def __init__(self, qualified_query):
        self.query = qualified_query

    def __getattr__(self, name):
        return getattr(self.query, name)

    def describe(self, *args, **kwargs):
        result = dict(self.query.describe(*args, **kwargs))
        result['dialogue_reading_extension'] = {
            'contract': 'dialogue-reading-maintenance-v1', 'status': 'opt_in_not_release_promoted',
            'views': ['physical', 'dialogue'], 'source_authority': 'unchanged qualified runtime',
            'legacy_fallback': False, 'new_evidence_identity': False}
        return result

    def read(self, corpus_ref, evidence_ref, before=0, after=0, limit=50, cursor=None,
             view='physical', locale='CHS'):
        helper = DialogueContext(self)
        if view == 'dialogue':
            if before or after:
                raise ValueError('dialogue view uses source connections, not before/after windows')
            return helper.read(corpus_ref, evidence_ref, limit, cursor, locale)
        if view != 'physical':
            raise ValueError('read view must be physical or dialogue')
        result = self.query.read(corpus_ref, evidence_ref, before, after, limit, cursor)
        parent, text = helper._container(evidence_ref)
        if helper.supports(parent):
            result['dialogue_source'] = helper.card(parent, text['locale'] if text is not None else locale)
        return result

    def follow(self, corpus_ref, evidence_ref, relation_kind=None, limit=50, cursor=None, resolve_targets=True):
        if relation_kind != 'dialogue_predecessor':
            return self.query.follow(corpus_ref, evidence_ref, relation_kind, limit, cursor, resolve_targets)
        result = DialogueContext(self).predecessors(corpus_ref, evidence_ref, limit, cursor)
        if not resolve_targets:
            for item in result['items']:
                for candidate in item['candidates']:
                    candidate['target_evidence_ref'] = None
        return result


class DialogueContext:
    def __init__(self, query):
        self.q = query

    def _mode(self, container):
        return self.q.components[container['source_key']].get('adapter')

    def supports(self, container):
        return self._mode(container) in {'narrative_dialog', 'quest_talk_physical'}

    def _container(self, ref):
        value = decode_ref(ref)
        if value['kind'] == 'container':
            return self.q._container_for(value), None
        text = self.q._text_for(value)
        parent = self.q.db.execute('SELECT * FROM research_container WHERE id=?',
                                   (text['container_id'],)).fetchone()
        self.q._recheck_container(parent)
        return parent, text

    def _physical(self, container):
        raw = dict(self.q._recheck_container(container))
        if 'raw_json' in raw and 'raw_sha256' in raw:
            if hashlib.sha256(raw['raw_json'].encode()).hexdigest() != raw['raw_sha256']:
                raise ValueError('dialogue physical record SHA drift')
        return raw

    def _texts(self, container, locale, fields=None):
        rows = self.q.db.execute('SELECT * FROM research_text WHERE container_id=? '
                                 'AND locale=? ORDER BY ordinal,id',
                                 (container['id'], locale)).fetchall()
        out = []
        for row in rows:
            if fields is not None and row['owner_field'] not in fields:
                continue
            text = self.q._recheck_text(row)
            item = {'evidence_ref': self.q._text_ref(row), 'raw_text': text,
                    'locale': row['locale'], 'owner_field': row['owner_field'],
                    'text_sha256': row['text_sha256'],
                    'provenance': self.q._text_provenance(row)}
            if hasattr(self.q, '_proof'):
                item['text_proof'] = self.q._proof(row)
            out.append(item)
        return out

    def _target(self, key, occurrence):
        component = self.q.components[key]
        rows = self.q.db.execute('SELECT * FROM research_container WHERE source_key=? '
                                 'AND evidence_run=? AND occurrence_id=? LIMIT 2',
                                 (key, component['run'], str(occurrence))).fetchall()
        if len(rows) != 1:
            raise ValueError('dialogue target missing or ambiguous in exact run')
        self.q._recheck_container(rows[0])
        return rows[0]

    def _ordinal_target(self, key, ordinal):
        component = self.q.components[key]
        rows = self.q.db.execute("SELECT * FROM research_container WHERE source_key=? "
            "AND evidence_run=? AND source_table='physical_row' AND pk_column='ordinal' AND pk_value=? LIMIT 2",
            (key, component['run'], str(ordinal))).fetchall()
        if len(rows) != 1:
            raise ValueError('dialogue ordinal target missing or ambiguous in exact run')
        self.q._recheck_container(rows[0])
        return rows[0]

    def _keys(self, adapter):
        if adapter is None:
            return []
        return sorted(k for k, v in self.q.components.items() if v.get('adapter') == adapter)

    def predecessors(self, corpus, evidence_ref, limit=20, cursor=None):
        """Inverse of a checked source edge, not previous database rows."""
        self.q._require(corpus)
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('dialogue predecessor limit must be 1..20')
        parent, text = self._container(evidence_ref)
        if not self.supports(parent):
            raise ValueError('dialogue predecessor requires Dialog/Talk evidence')
        scope = {'op': 'dialogue-predecessors-v1', 'anchor': evidence_ref, 'limit': limit}
        after = self.q._after(cursor, scope)
        if type(after) is not int:
            raise ValueError('dialogue predecessor cursor mismatch')
        src = self.q._source(parent['source_key']); items = []
        if self._mode(parent) == 'narrative_dialog':
            rows = src.execute("SELECT DISTINCT row_ordinal FROM physical_candidate "
                "WHERE field='nextDialogs' AND target_source_path=? AND target_ordinal=? "
                "AND row_ordinal>? ORDER BY row_ordinal LIMIT ?",
                (parent['source_path'], int(parent['pk_value']), after - 1, limit + 1)).fetchall()
            for r in rows[:limit]:
                previous = self._ordinal_target(parent['source_key'], r['row_ordinal'])
                links = [e for e in self._dialog_links(previous, 'nextDialogs')
                         if self.q._container_ref(parent) in e['target_refs']]
                if not links:
                    raise ValueError('reverse dialogue edge failed forward recheck')
                items.append({'relation_kind': 'dialogue_predecessor', 'direction': 'inbound',
                              'source_evidence_ref': evidence_ref, 'status': 'physical_candidate',
                              'candidates': [{'target_evidence_ref': self.q._container_ref(previous)}],
                              'forward_proof': links})
            position = rows[len(items)-1]['row_ordinal'] + 1 if items else after
        else:
            node = self._node(parent, text, 'CHS')
            rows = src.execute("SELECT DISTINCT n.ordinal,n.id FROM talk_node n, "
                "json_each(n.successors_json) e,json_each(e.value,'$.local_candidates') t "
                "WHERE n.container_id=? AND t.value=? AND n.ordinal>=? "
                "ORDER BY n.ordinal,n.id LIMIT ?", (parent['occurrence_id'], node, after, limit+1)).fetchall()
            for r in rows[:limit]:
                texts, edges = self._talk_node(parent, r['id'], 'CHS')
                items.append({'relation_kind': 'dialogue_predecessor', 'direction': 'inbound',
                    'source_evidence_ref': evidence_ref, 'status': 'physical_candidate',
                    'candidates': [{'target_evidence_ref': t['evidence_ref']} for t in texts],
                    'forward_proof': [e for e in edges if node in e['target_nodes']],
                    'node': r['id'], 'body_status': 'available' if texts else 'no_resolved_body_in_locale'})
            position = rows[len(items)-1]['ordinal'] + 1 if items else after
        more = len(rows) > len(items)
        return {'items': items, 'next_cursor': self.q._cursor(scope, position) if more else None,
                'delivered_count': len(items), 'scope_complete': not more, 'partial': more,
                'logical_identity_proven': False,
                'relation_semantics': 'inverse of source-authored local dialogue connections; not narrative chronology'}

    def _dialog_links(self, container, field):
        """Read only frozen source-authored locator/candidate rows, in their order."""
        src = self.q._source(container['source_key'])
        physical = self._physical(container)
        original = json.loads(physical['raw_json'])
        links = []
        for row in src.execute('SELECT * FROM locator WHERE row_ordinal=? AND field=? '
                               'ORDER BY item_ordinal', (physical['ordinal'], field)):
            row = dict(row)
            authored = original.get(field)
            if field == 'nextDialogs':
                authored = [] if row['item_ordinal'] == -1 else authored[row['item_ordinal']]
            if json.loads(row['raw_value']) != authored:
                raise ValueError('dialogue locator differs from source-authored field')
            targets = []
            for candidate in src.execute('SELECT * FROM physical_candidate WHERE row_ordinal=? '
                                         'AND field=? AND item_ordinal=? ORDER BY target_ordinal',
                                         (physical['ordinal'], field, row['item_ordinal'])):
                candidate = dict(candidate)
                adapter = {'npc': 'narrative_npc', 'dialog': 'narrative_dialog'}.get(row['target_family'])
                for key in self._keys(adapter):
                    target = self._ordinal_target(key, candidate['target_ordinal'])
                    target_raw = self._physical(target)
                    if (target['source_path'], target['source_pointer']) != (
                            candidate['target_source_path'], candidate['target_pointer']):
                        raise ValueError('dialogue target coordinates drift')
                    native = authored.get('id') if field == 'talkRole' else authored
                    if str(target_raw['native_key']) != str(native):
                        raise ValueError('dialogue target native locator drift')
                    targets.append(self.q._container_ref(target))
            if len(targets) != row['candidate_count']:
                raise ValueError('dialogue locator target count/binding mismatch')
            links.append({'kind': field, 'position': row['item_ordinal'],
                          'status': row['state'], 'raw_locator': authored,
                          'source_evidence_ref': self.q._container_ref(container),
                          'proof_pointer': container['source_pointer'] + '/' + field,
                          'target_refs': targets, 'logical_identity_proven': False})
        return links

    def _speaker(self, container, locale):
        if self._mode(container) != 'narrative_dialog':
            return {'status': 'unresolved', 'display_name': None,
                    'reason': 'this Talk projection does not declare a speaker resolver'}
        links = self._dialog_links(container, 'talkRole')
        names = []
        for link in links:
            for ref in link['target_refs']:
                parent, _ = self._container(ref)
                for name in self._texts(parent, locale, {'nameTextMapHash'}):
                    names.append({**name, 'role_record_ref': ref})
        # Equal strings at different occurrences remain multiple candidates.
        resolved = (len(links) == 1 and links[0]['status'] == 'resolved_unique'
                    and len(links[0]['target_refs']) == 1 and len(names) == 1)
        return {'status': 'source_role_resolved' if resolved else 'ambiguous' if names else 'unresolved',
                'display_name': names[0]['raw_text'] if resolved else None,
                'names': names, 'role_references': links,
                'basis': 'source talkRole -> exact NPC physical row -> locale name field',
                'logical_identity_proven': False,
                'reason': None if resolved else 'role discriminator, target or locale name is not uniquely resolved'}

    def _quest_locations(self, container, locale, limit=8):
        """Invert frozen Quest->Talk candidates, then MainQuest->Quest candidates.

        DialogExcel and BinOutput/Talk IDs are different namespaces: no ID join.
        """
        if self._mode(container) != 'quest_talk_physical':
            return [], False, 'no declared DialogExcel-to-quest physical link'
        src = self.q._source(container['source_key'])
        parents = src.execute("SELECT DISTINCT p.* FROM physical_reference r, "
                              "json_each(r.candidates_json) c JOIN physical_container p "
                              "ON p.id=r.container_id WHERE json_extract(c.value,'$.container_id')=? "
                              "AND r.kind='quest_to_talk' AND r.ingest_run_id=? AND p.ingest_run_id=? "
                              "AND p.kind='quest_root' ORDER BY p.id LIMIT ?",
                              (container['occurrence_id'], container['evidence_run'], container['evidence_run'], limit + 1)).fetchall()
        candidates = []
        if self._physical(container)['kind'] == 'quest_root':
            parents = [self._physical(container)]
        for p in parents[:limit]:
            parent = self._target(container['source_key'], p['id'])
            if parent['occurrence_id'] == container['occurrence_id']:
                proofs = []
            else:
                proofs = []
                for r in src.execute("SELECT * FROM physical_reference WHERE container_id=? "
                                     "AND kind='quest_to_talk' AND ingest_run_id=? ORDER BY id",
                                     (p['id'], container['evidence_run'])):
                    for c in json.loads(r['candidates_json']):
                        if c['container_id'] == container['occurrence_id']:
                            if (c['path'], c['pointer'], c['kind']) != (
                                    container['source_path'], container['source_pointer'], self._physical(container)['kind']):
                                raise ValueError('Quest->Talk physical target drift')
                            proofs.append({'source_evidence_ref': self.q._container_ref(parent),
                                           'pointer': r['pointer'], 'status': r['status'],
                                           'raw_locator': json.loads(r['raw_json'])})
            for key in self._keys('quest_narrative_archive'):
                qs = self.q._source(key)
                roots = qs.execute('SELECT r.* FROM main_root_candidate r JOIN excel_row e ON e.id=r.main_row_id '
                                   'WHERE r.quest_talk_container_id=? AND e.run_id=? '
                                   'ORDER BY r.id LIMIT ?', (p['id'], self.q.components[key]['run'], limit + 1)).fetchall()
                for root in roots[:limit]:
                    if root['quest_source_path'] != parent['source_path']:
                        raise ValueError('MainQuest->Quest source path drift')
                    main = self._target(key, root['main_row_id'])
                    candidates.append({'container_ref': self.q._container_ref(main),
                        'titles': self._texts(main, locale, {'titleTextMapHash'}),
                        'status': 'physical_candidate', 'logical_identity_proven': False,
                        'chain': [*proofs, {'source_evidence_ref': self.q._container_ref(main),
                            'target_evidence_ref': self.q._container_ref(parent),
                            'physical_candidate': dict(root)}]})
                if len(candidates) >= limit:
                    return candidates[:limit], True, 'location candidate display bound reached'
        return candidates, len(parents) > limit, None if candidates else 'no MainQuest title binding found in declared projection'

    def card(self, container, locale='CHS'):
        locations, truncated, reason = self._quest_locations(container, locale)
        return {'physical_source': self.q._container_payload(container),
                'speaker': self._speaker(container, locale),
                'locations': locations, 'location_candidates_truncated': truncated,
                'location_unresolved_reason': reason, 'scene_display_name': None,
                'scene_status': 'unresolved', 'narrative_chronology_proven': False}

    def _node(self, parent, text, locale):
        if self._mode(parent) == 'narrative_dialog':
            return parent['id']
        if text is not None and text['source_table'] == 'text_slot':
            from ..research71_candidate_adapters import fetch
            source = fetch(self.q._source(text['source_key']), 'text_slot', text['pk_column'], text['pk_value'])
            return source['node_id']
        first = self.q._source(parent['source_key']).execute('SELECT id FROM talk_node WHERE container_id=? '
                    'ORDER BY ordinal,id LIMIT 1', (parent['occurrence_id'],)).fetchone()
        return first['id'] if first else None

    def _talk_node(self, parent, node_id, locale):
        src = self.q._source(parent['source_key'])
        node = src.execute('SELECT * FROM talk_node WHERE id=?', (node_id,)).fetchone()
        if node is None or node['container_id'] != parent['occurrence_id'] or node['ingest_run_id'] != parent['evidence_run']:
            raise ValueError('Talk node escaped physical container/run')
        rows = src.execute('SELECT id FROM text_slot WHERE node_id=? AND locale=?', (node_id, locale)).fetchall()
        texts = []
        for row in rows:
            hits = self.q.db.execute('SELECT * FROM research_text WHERE container_id=? AND source_key=? '
                       'AND source_table=\'text_slot\' AND pk_value=?',
                       (parent['id'], parent['source_key'], row['id'])).fetchall()
            for hit in hits:
                raw = self.q._recheck_text(hit)
                texts.append({'evidence_ref': self.q._text_ref(hit), 'raw_text': raw,
                              'locale': locale, 'owner_field': hit['owner_field'],
                              'text_sha256': hit['text_sha256'], 'provenance': self.q._text_provenance(hit),
                              'text_proof': self.q._proof(hit)})
        edges = []
        for s in json.loads(node['successors_json']):
            refs = []
            for target_id in s['local_candidates']:
                target = src.execute('SELECT container_id,ingest_run_id FROM talk_node WHERE id=?', (target_id,)).fetchone()
                if target is None or (target['container_id'], target['ingest_run_id']) != (parent['occurrence_id'], parent['evidence_run']):
                    raise ValueError('Talk successor escaped container/run')
                refs.append(target_id)
            edges.append({'kind': 'source_successor', 'position': s['ordinal'],
                          'status': s['status'], 'raw_locator': s['raw_target_id'],
                          'target_nodes': refs, 'external_candidates': s['physical_candidates'],
                          'source_pointer': node['pointer'], 'logical_identity_proven': False})
        return texts, edges

    def read(self, corpus, evidence_ref, limit=8, cursor=None, locale='CHS'):
        self.q._require(corpus)
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('dialogue view limit must be 1..20')
        if locale not in {'CHS', 'EN', 'JP'}:
            raise ValueError('dialogue view locale must be CHS/EN/JP')
        parent, text = self._container(evidence_ref)
        if not self.supports(parent):
            raise ValueError('dialogue view requires a declared Dialog or Quest/Talk projection')
        scope = {'op': 'dialogue-view-v1', 'anchor': evidence_ref, 'limit': limit, 'locale': locale}
        state = self.q._after(cursor, scope)
        anchor = self._node(parent, text, locale)
        if state == 0:
            state = {'frontier': [anchor] if anchor else [], 'visited': []}
        if not isinstance(state, dict) or set(state) != {'frontier', 'visited'}:
            raise ValueError('dialogue continuation state mismatch')
        if any(not isinstance(state[k], list) or len(state[k]) > MAX_VISITED for k in state):
            raise ValueError('dialogue continuation exceeds bounded state')
        frontier, visited = list(state['frontier']), list(state['visited'])
        nodes = []; unresolved = False
        card = self.card(parent, locale)
        while frontier and len(nodes) < limit and len(visited) < MAX_VISITED:
            current = frontier.pop(0)
            if current in visited:
                continue
            if self._mode(parent) == 'narrative_dialog':
                if type(current) is not int:
                    raise ValueError('dialogue continuation node must be an index locator')
                current_parent = self.q.db.execute('SELECT * FROM research_container WHERE id=? AND source_key=? '
                    'AND evidence_run=?', (current, parent['source_key'], parent['evidence_run'])).fetchone()
                if current_parent is None:
                    raise ValueError('dialogue continuation escaped source scope')
                self.q._recheck_container(current_parent)
                texts = self._texts(current_parent, locale, {'talkContentTextMapHash'})
                edges = self._dialog_links(current_parent, 'nextDialogs')
                speaker = self._speaker(current_parent, locale)
                targets = [decode_ref(r)['index'] for e in edges for r in e['target_refs']]
            else:
                current_parent = parent
                texts, edges = self._talk_node(parent, current, locale)
                speaker = card['speaker']
                targets = [r for e in edges for r in e['target_nodes']]
            item = {'node': self.q._container_ref(current_parent) if self._mode(parent)=='narrative_dialog' else current,
                    'container_ref': self.q._container_ref(current_parent),
                    'texts': texts, 'speaker': speaker, 'edges': edges,
                    'body_status': 'available' if texts else 'no_resolved_body_in_locale'}
            if len(json.dumps({'nodes': nodes + [item], 'source_context': card}, ensure_ascii=False).encode()) > MAX_PAGE_BYTES:
                frontier.insert(0, current)
                if not nodes:
                    raise ValueError('one dialogue node exceeds view budget; use ordinary read or character-range continuation')
                break
            nodes.append(item); visited.append(current)
            unresolved |= not texts or any(e['status'] not in {'resolved_unique', 'terminal_empty', 'local'} for e in edges)
            frontier.extend(t for t in targets if t not in visited and t not in frontier)
            if len(frontier) > MAX_VISITED:
                raise ValueError('dialogue frontier exceeds bounded continuation; follow individual branch refs')
        frontier = [n for n in frontier if n not in visited]
        cap = bool(frontier) and len(visited) >= MAX_VISITED
        return {'evidence_ref': evidence_ref, 'view': 'dialogue', 'nodes': nodes,
                'source_context': card, 'delivered_count': len(nodes),
                'next_cursor': self.q._cursor(scope, {'frontier': frontier, 'visited': visited}) if frontier and not cap else None,
                'continuation_available': bool(frontier) and not cap,
                'traversal_limit_reached': cap,
                'limit_action': 'follow supplied branch refs individually' if cap else None,
                'reachable_local_graph_exhausted': not frontier,
                'partial': bool(frontier) or unresolved or card['location_candidates_truncated'] or bool(card['location_unresolved_reason']),
                'coverage': {'locale': locale, 'visited_nodes': len(visited), 'page_nodes': len(nodes)},
                'ordering_basis': 'breadth-first reading view over source-authored connections; not playback or narrative chronology',
                'logical_identity_proven': False, 'corpus_complete': False,
                'recommended_next_actions': ['read_evidence with returned cursor', 'inspect_evidence for cited text refs']}
