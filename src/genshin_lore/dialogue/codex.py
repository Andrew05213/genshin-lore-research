"""Read existing Codex speaker/body slots together; no new identity or index.

The sealed node-like projection supplies physical boundaries. Source labels
are displayed as labels, including new-wrapper/unresolved proof restrictions.
Name equality discovers candidates, never joins a logical NPC to a story actor.
"""
from __future__ import annotations

import hashlib
import json

from ..research71_candidate_adapters import fetch
from ..codexquest_source import SHAPES

BODY_LABELS = {'DialogNormal', 'Narratage', 'Aside'}
MAX_BYTES = 80_000


def at_pointer(value, pointer):
    for part in pointer.split('/')[1:]:
        part = part.replace('~1', '/').replace('~0', '~')
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


class CodexDialogue:
    def __init__(self, context):
        self.context = context
        self.q = context.q
        self.roots = {}

    def root(self, parent):
        identity = parent['source_key'], parent['occurrence_id']
        if identity not in self.roots:
            raw = self.context._physical(parent)
            data = bytes(raw['raw_bytes'])
            if hashlib.sha256(data).hexdigest() != raw['raw_sha256']:
                raise ValueError('Codex source bytes SHA drift')
            if raw['raw_sha256'] != parent['source_file_sha256']:
                raise ValueError('Codex source identity differs from qualified index')
            self.roots[identity] = (json.loads(data), raw['shape'])
        return self.roots[identity][0]

    def child(self, parent, child):
        raw = child['raw_json']
        if hashlib.sha256(raw.encode()).hexdigest() != child['raw_sha256']:
            raise ValueError('Codex child SHA drift')
        if json.loads(raw) != at_pointer(self.root(parent), child['source_pointer']):
            raise ValueError('Codex child differs from its exact source pointer')
        return child

    def slot(self, parent, row):
        src = self.q._source(parent['source_key'])
        candidate = fetch(src, row['source_table'], row['pk_column'], row['pk_value'])
        slot = src.execute('SELECT * FROM codex_text_slot WHERE id=?',
                           (candidate['slot_id'],)).fetchone()
        if slot is None or slot['root_id'] != parent['occurrence_id']:
            raise ValueError('Codex text slot escaped its physical root')
        atom = at_pointer(self.root(parent), slot['source_pointer'])
        shape = SHAPES.get(self.roots[(parent['source_key'], parent['occurrence_id'])][1])
        hash_key = shape['locator_hash'] if shape else 'textId'
        label_key = shape['locator_label'] if shape else 'DFKLAEHPOBE'
        if (not isinstance(atom, dict) or str(atom.get(hash_key)) != slot['raw_hash']
                or atom.get(label_key) != slot['source_label']):
            raise ValueError('Codex source label/hash differs from sealed slot: '
                             + parent['source_path'] + ' ' + slot['source_pointer'])
        return slot

    def text(self, parent, row):
        slot = self.slot(parent, row)
        value = self.q._recheck_text(row)
        return {'evidence_ref': self.q._text_ref(row), 'raw_text': value,
                'locale': row['locale'], 'owner_field': slot['source_label'],
                'source_owner_pointer': slot['source_pointer'],
                'text_sha256': row['text_sha256'],
                'provenance': self.q._text_provenance(row),
                'text_proof': self.q._proof(row)}

    def node_for(self, parent, row):
        pointer = self.slot(parent, row)['source_pointer']
        ancestors = []
        while '/' in pointer:
            pointer = pointer.rsplit('/', 1)[0]
            ancestors.append(pointer)
        src = self.q._source(parent['source_key'])
        rows = src.execute('SELECT * FROM codex_child WHERE root_id=? '
            "AND kind='node_like' AND source_pointer IN (" + ','.join('?' for _ in ancestors) + ')',
            (parent['occurrence_id'], *ancestors)).fetchall()
        if not rows:
            return None
        return self.child(parent, max(rows, key=lambda r: len(r['source_pointer'])))

    def node(self, parent, child, locale):
        child = self.child(parent, child)
        src = self.q._source(parent['source_key'])
        prefix = child['source_pointer'] + '/'
        slots = src.execute('SELECT * FROM codex_text_slot WHERE root_id=? AND locale=? '
            'AND substr(source_pointer,1,?)=? ORDER BY source_pointer,id',
            (parent['occurrence_id'], locale, len(prefix), prefix)).fetchall()
        texts, names = [], []
        labels = []
        for slot in slots:
            label = slot['source_label']
            labels.append(label)
            if label not in BODY_LABELS and not label.startswith('Speaker'):
                continue
            for candidate in src.execute('SELECT id FROM codex_text_candidate WHERE slot_id=? ORDER BY id', (slot['id'],)):
                rows = self.q.db.execute('SELECT * FROM research_text WHERE source_key=? '
                    'AND container_id=? AND source_table=\'codex_text_candidate\' AND pk_value=?',
                    (parent['source_key'], parent['id'], candidate['id'])).fetchall()
                if len(rows) != 1:
                    raise ValueError('qualified Codex text occurrence missing or duplicated')
                item = self.text(parent, rows[0])
                (texts if label in BODY_LABELS else names).append(item)
        # A unique displayed label is not a unique actor or accepted role map.
        speaker = {'display_name': names[0]['raw_text'] if len(names) == 1 else None,
                   'status': 'source_label_observed' if len(names) == 1 else 'ambiguous' if names else 'unresolved',
                   'names': names, 'logical_identity_proven': False,
                   'basis': 'source-labelled slots in the same sealed node-like physical record; not logical NPC ownership'}
        return {'node': child['id'], 'container_ref': self.q._container_ref(parent),
                'source_pointer': child['source_pointer'], 'texts': texts, 'speaker': speaker,
                'observed_source_labels': sorted(set(labels)),
                'body_status': 'available' if texts else 'no_resolved_body_in_locale',
                'logical_identity_proven': False}

    def titles(self, parent, child, locale):
        section = child['source_pointer'].rsplit('/', 2)[0]
        result = []
        for row in self.q.db.execute('SELECT * FROM research_text WHERE container_id=? AND locale=? '
            "AND owner_field IN ('MainQuestTitle','ChapterTitle','SubQuestTitle') ORDER BY id",
            (parent['id'], locale)):
            slot = self.slot(parent, row)
            pointer = slot['source_pointer']
            if (slot['source_label'] != 'SubQuestTitle' and pointer.count('/') == 1
                    or slot['source_label'] == 'SubQuestTitle' and pointer.rsplit('/', 1)[0] == section):
                result.append(self.text(parent, row))
        return result

    def sequence(self, parent, child):
        prefix = child['source_pointer'].rsplit('/', 1)[0] + '/'
        src = self.q._source(parent['source_key'])
        return [r for r in src.execute("SELECT * FROM codex_child WHERE root_id=? AND kind='node_like' "
            'AND substr(source_pointer,1,?)=? ORDER BY physical_ordinal,id',
            (parent['occurrence_id'], len(prefix), prefix))
            if r['source_pointer'].rsplit('/', 1)[0] + '/' == prefix]

    def card(self, parent, child, locale):
        value = self.node(parent, child, locale)
        sequence = self.sequence(parent, child)
        pos = next(i for i, r in enumerate(sequence) if r['id'] == child['id'])
        value['source_context'] = {
            'source_path': parent['source_path'], 'titles': self.titles(parent, child, locale),
            'before': [self.node(parent, sequence[pos-1], locale)] if pos else [],
            'after': [self.node(parent, sequence[pos+1], locale)] if pos+1 < len(sequence) else [],
            'ordering_basis': 'neighbours in the same physical section array; not playback, branch or narrative chronology',
            'section_start': pos == 0, 'section_end': pos+1 == len(sequence)}
        return value

    def read(self, corpus, anchor, parent, text, limit, cursor, locale):
        npc = parent['evidence_family'] == 'npc_archive'
        names = self.context._texts(parent, locale, {'nameTextMapHash'}) if npc else []
        scope = {'op': 'codex-source-dialogue-v1', 'anchor': anchor, 'locale': locale,
                 'limit': limit, 'names': [n['evidence_ref'] for n in names]}
        state = self.q._after(cursor, scope)
        if type(state) is not int or state < 0:
            raise ValueError('Codex dialogue cursor mismatch')
        nodes, position = [], state
        if npc:
            keys = self.context._keys('codexquest_archive')
            values = list(dict.fromkeys(n['raw_text'] for n in names))  # search strings only; records stay separate
            if keys and values:
                rows = self.q.db.execute('SELECT * FROM research_text WHERE evidence_family=\'codexquest_archive\' '
                    'AND source_key IN (' + ','.join('?' for _ in keys) + ') AND raw_text IN (' +
                    ','.join('?' for _ in values) + ') AND owner_field=\'SpeakerKnown\' AND locale=? '
                    'AND id>? ORDER BY id LIMIT ?', (*keys, *values, locale, state, limit+1)).fetchall()
            else:
                rows = []
            selected = []
            for row in rows:
                root = self.q.db.execute('SELECT * FROM research_container WHERE id=?', (row['container_id'],)).fetchone()
                matched = self.text(root, row)
                selected.append((row['id'], root, self.node_for(root, row), matched['evidence_ref']))
        else:
            if text is None:
                raise ValueError('Codex dialogue reading requires a returned speaker/body text ref')
            child = self.node_for(parent, text)
            if child is None:
                raise ValueError('Codex text has no projected node-like physical boundary')
            sequence = self.sequence(parent, child)
            start = next(i for i, r in enumerate(sequence) if r['id'] == child['id'])
            selected = [(i+1, parent, r, None) for i, r in enumerate(sequence[start:])][state:state+limit+1]
        for identity, root, child, matched in selected[:limit]:
            item = self.card(root, child, locale) if child is not None else {
                'node': None, 'texts': [], 'body_status': 'no_projected_node_boundary',
                'container_ref': self.q._container_ref(root)}
            if matched is not None:
                item['matched_speaker_ref'] = matched
            if len(json.dumps({'nodes': nodes+[item], 'name_candidates': names}, ensure_ascii=False).encode()) > MAX_BYTES:
                if not nodes:
                    raise ValueError('one Codex dialogue record exceeds view budget; use ordinary exact reads')
                break
            nodes.append(item)
            position = identity
        more = len(selected) > len(nodes)
        return {'evidence_ref': anchor, 'view': 'dialogue', 'nodes': nodes,
                'name_candidates': names, 'match_basis': 'exact source speaker label/name candidate' if npc else 'exact physical node',
                'delivered_count': len(nodes),
                'next_cursor': self.q._cursor(scope, position) if more else None,
                'continuation_available': more, 'scope_complete': not more,
                'partial': True, 'corpus_complete': False, 'logical_identity_proven': False,
                'coverage': {'locale': locale, 'scope': 'qualified CodexQuest source-labelled nodes only; not all NPC speech',
                             'eof_scope': 'matching source-name occurrences' if npc else 'same physical section from this anchor',
                             'other_dialogue_sources': 'existing Dialog/Talk navigation remains separate',
                             'physical_occurrences_not_deduplicated': True},
                'ordering_basis': 'stable source occurrence enumeration; context follows physical section arrays, not chronology',
                'recommended_next_actions': ['read_evidence on returned text refs with view=dialogue for surrounding source text',
                                             'read_evidence with the supplied cursor for more records',
                                             'inspect_evidence on final text/name/title refs']}
