"""Read-only, exact-run Quest-derived 7.1 discovery; never a full Research corpus."""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

SOURCE_COMMIT = '9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'
SOURCE_TREE = 'aa578c08c402af39b41f1ffc95124d10f5410272'
SNAPSHOT = 'anime-game-data:7.1:' + SOURCE_COMMIT
OLD_CODEX = {'rc_493': ('EBNBLBEIFFJ', 'PEAKPGNONFA'),
             'legacy_7': ('HLCINEMBGEF', 'IIJIPINAOOF'),
             'alternate_3': ('MEGJPCLADOG', 'FCEMMMMDKLP')}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _open(path: Path) -> sqlite3.Connection:
    if not path.is_file() or any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('artifact missing or SQLite sidecar: ' + str(path))
    db = sqlite3.connect(path.absolute().as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    return db


def _pointer(value: object, pointer: str) -> object:
    if not pointer.startswith('/'):
        raise ValueError('physical JSON pointer required')
    for part in pointer[1:].split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


class QuestDerived71:
    """Explicit artifact bindings; all navigation remains physical/candidate-only."""

    def __init__(self, *, source: Path, manifest: Path, narrative: Path, review: Path,
                 review_binding: Path, codex: Path, quest_talk: Path) -> None:
        self.source = Path(source)
        self.manifest_path = Path(manifest)
        if subprocess.check_output(['git', '-C', str(self.source), 'rev-parse', 'HEAD', 'HEAD^{tree}'], text=True).splitlines() != [SOURCE_COMMIT, SOURCE_TREE]:
            raise ValueError('source snapshot mismatch')
        self.manifest = json.loads(self.manifest_path.read_text())
        if self.manifest['source_snapshot'] != SNAPSHOT or self.manifest['product_accepted'] is not False:
            raise ValueError('candidate manifest authority mismatch')
        components = self.manifest['components']
        self.paths = {'quest_narrative_archive': Path(narrative), 'quest_review_archive': Path(review), 'codexquest_archive': Path(codex)}
        for family, path in self.paths.items():
            entry = components[family]
            if entry['acceptance_status'] != 'PHYSICAL_FOUNDATION_ACCEPTED' or path.stat().st_size != entry['artifact']['size_bytes'] or _sha(path) != entry['artifact']['sha256']:
                raise ValueError('candidate artifact mismatch ' + family)
        self.runs = {family: components[family]['ingest_run_id'] for family in self.paths}
        self.db = {family: _open(path) for family, path in self.paths.items()}
        binding_authority = json.loads((Path(manifest).parent / 'quest-review-binding-v2.json').read_text())
        if (binding_authority['acceptance_status'] != 'ACCEPTED_EXACT_7_1_REPLACEMENT_COMPONENT'
                or binding_authority['source_snapshot'] != SNAPSHOT
                or binding_authority['base_review_manifest_sha256'] != _sha(Path(manifest))
                or Path(review_binding).stat().st_size != binding_authority['artifact']['size_bytes']
                or _sha(Path(review_binding)) != binding_authority['artifact']['sha256']):
            raise ValueError('Review binding acceptance seal')
        self.binding = _open(Path(review_binding))
        bound_release = json.loads(self.binding.execute('SELECT manifest_json FROM release').fetchone()[0])
        self.binding_run = bound_release['ingest_run_id']
        if (bound_release['contract'] != binding_authority['contract'] or bound_release['snapshot'] != SNAPSHOT
                or bound_release['product_accepted'] is not False
                or bound_release['parser_build_id'] != binding_authority['parser_build_id']
                or bound_release['ingest_run_id'] != binding_authority['ingest_run_id']):
            raise ValueError('Review binding contract')
        for key, family in (('review', 'quest_review_archive'), ('narrative', 'quest_narrative_archive')):
            dependency = bound_release['dependencies'][key]
            if dependency['artifact'] != components[family]['artifact'] or dependency['ingest_run_id'] != self.runs[family]:
                raise ValueError('Review binding dependency')
        self.quest_talk_path = Path(quest_talk)
        self.quest_talk = _open(self.quest_talk_path)
        qt_manifest = json.loads((Path(manifest).parents[1] / 'quest-talk-7.1/standalone-candidate.json').read_text())
        if self.quest_talk_path.stat().st_size != qt_manifest['artifact']['size_bytes'] or _sha(self.quest_talk_path) != qt_manifest['artifact']['sha256']:
            raise ValueError('Quest/Talk artifact mismatch')
        self.runs['quest_talk_physical'] = qt_manifest['ingest_run_id']
        self.runs['quest_review_binding'] = self.binding_run
        self.codex_capability = None

    def close(self) -> None:
        for db in (*self.db.values(), self.binding, self.quest_talk):
            db.close()

    def _scope(self, family: str, run: str) -> None:
        if family not in self.runs or self.runs[family] != run:
            raise ValueError('explicit exact run required; no default/latest/sibling fallback')

    @staticmethod
    def _cursor(scope: dict, after: str) -> str:
        value = {'scope': scope, 'after': after}
        return base64.urlsafe_b64encode(_canonical(value).encode()).decode().rstrip('=')

    @staticmethod
    def _after(cursor: str | None, scope: dict) -> str:
        if cursor is None:
            return ''
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
        except (ValueError, UnicodeError) as exc:
            raise ValueError('invalid cursor') from exc
        if value.get('scope') != scope or not isinstance(value.get('after'), str):
            raise ValueError('cursor scope mismatch')
        return value['after']

    def search(self, *, family: str, run: str, literal: str, limit: int = 20, cursor: str | None = None) -> dict:
        self._scope(family, run)
        if not literal or not 1 <= limit <= 50:
            raise ValueError('bounded nonempty literal and limit 1..50 required')
        scope = {'family': family, 'run': run, 'literal': literal, 'limit': limit, 'mode': 'literal'}
        after = self._after(cursor, scope)
        if family == 'quest_narrative_archive':
            sql = '''SELECT c.id AS hit_id,c.raw_text,r.id AS evidence_id,r.kind,r.source_path,r.pointer AS source_pointer
                     FROM text_candidate c JOIN text_slot s ON s.id=c.slot_id JOIN excel_row r ON r.id=s.row_id
                     WHERE c.id>? AND instr(c.raw_text,?)>0 ORDER BY c.id LIMIT ?'''
        elif family == 'quest_review_archive':
            sql = '''SELECT c.id AS hit_id,c.raw_text,r.id AS evidence_id,r.kind,r.source_path,r.source_pointer
                     FROM text_candidate c JOIN text_slot s ON s.id=c.slot_id JOIN evidence_row r ON r.id=s.row_id
                     WHERE c.id>? AND instr(c.raw_text,?)>0 ORDER BY c.id LIMIT ?'''
        elif family == 'codexquest_archive':
            sql = '''SELECT c.id AS hit_id,c.raw_text,r.id AS evidence_id,r.shape AS kind,r.source_path,s.source_pointer
                     FROM codex_text_candidate c JOIN codex_text_slot s ON s.id=c.slot_id JOIN codex_root r ON r.id=s.root_id
                     WHERE c.id>? AND instr(c.raw_text,?)>0 ORDER BY c.id LIMIT ?'''
        else:
            raise ValueError('unsupported bounded family')
        rows = [dict(row) for row in self.db[family].execute(sql, (after, literal, limit + 1))]
        more = len(rows) > limit
        visible = rows[:limit]
        for row in visible:
            row['evidence_ref'] = {'family': family, 'run': run, 'id': row['evidence_id']}
            row['semantic_mapping'] = self._semantic(family, row['evidence_id'])
        return {'snapshot': SNAPSHOT, 'product_accepted': False, 'coverage': 'Quest-derived 7.1 candidates only', 'partial': True,
                'results': visible, 'next_cursor': self._cursor(scope, visible[-1]['hit_id']) if more and visible else None}

    def _semantic(self, family: str, evidence_id: str) -> str:
        if family == 'quest_review_archive':
            return 'versioned_exact_target_binding; relation_mapping_unresolved'
        if family == 'quest_narrative_archive':
            if self.db[family].execute('SELECT 1 FROM child_observation WHERE id=?', (evidence_id,)).fetchone():
                return 'versioned_physical_child_locator; semantic_order_unresolved'
            return 'exact_excel_physical; child_semantic_mapping_partial'
        if family == 'codexquest_archive':
            row = self.db[family].execute('SELECT shape FROM codex_root WHERE id=?', (evidence_id,)).fetchone()
            if row is None:
                child = self.db[family].execute('SELECT root_id,kind,source_pointer FROM codex_child WHERE id=?', (evidence_id,)).fetchone()
                if child is None:
                    return 'unresolved'
                parent = self.db[family].execute('SELECT shape FROM codex_root WHERE id=?', (child['root_id'],)).fetchone()
                return 'historical_equivalent' if parent['shape'] in OLD_CODEX else 'versioned_physical_section_or_node'
            if row['shape'] in OLD_CODEX:
                return 'historical_equivalent'
            return 'metadata_only_root' if row['shape'] == 'new_metadata_only' else 'versioned_physical_section_or_node'
        return 'physical_only'

    def read(self, ref: dict, *, child_limit: int = 20) -> dict:
        family, run, evidence_id = ref['family'], ref['run'], ref['id']
        self._scope(family, run)
        if not 0 <= child_limit <= 100:
            raise ValueError('child_limit 0..100')
        if family == 'quest_narrative_archive':
            row = self.db[family].execute('SELECT * FROM excel_row WHERE id=?', (evidence_id,)).fetchone()
            if row is None:
                child = self.db[family].execute('SELECT * FROM child_observation WHERE id=?', (evidence_id,)).fetchone()
                if child is None:
                    raise KeyError(ref)
                source_raw = (self.source / child['quest_source_path']).read_bytes()
                source_value = json.loads(source_raw.decode('utf-8-sig'))
                physical = _pointer(source_value, child['source_pointer'])
                qt_child = self.quest_talk.execute('SELECT * FROM quest_child WHERE id=?', (child['quest_talk_child_id'],)).fetchone()
                qt_container = self.quest_talk.execute('SELECT source_path,ingest_run_id,raw_sha256 FROM physical_container WHERE id=?', (child['quest_talk_container_id'],)).fetchone()
                if (not isinstance(physical, dict) or qt_child is None or qt_child['container_id'] != child['quest_talk_container_id']
                        or qt_container is None or qt_container['source_path'] != child['quest_source_path']
                        or qt_container['ingest_run_id'] != self.runs['quest_talk_physical']
                        or hashlib.sha256(source_raw).hexdigest() != qt_container['raw_sha256']
                        or qt_child['ingest_run_id'] != self.runs['quest_talk_physical']
                        or qt_child['pointer'] != child['source_pointer']
                        or qt_child['ordinal'] != child['physical_ordinal']
                        or json.loads(qt_child['raw_json']) != physical
                        or hashlib.sha256(qt_child['raw_json'].encode()).hexdigest() != qt_child['raw_sha256']):
                    raise ValueError('Narrative child exact accepted Quest/Talk source recheck')
                result = {'container': dict(child), 'physical_child_observation': True}
            else:
                raw_row = json.loads((self.source / row['source_path']).read_bytes().decode('utf-8-sig'))[row['ordinal']]
                if json.loads(row['raw_json']) != raw_row or hashlib.sha256(row['raw_json'].encode()).hexdigest() != row['raw_sha256']:
                    raise ValueError('Narrative row source recheck')
                texts = [dict(x) for x in self.db[family].execute('SELECT s.owner_field,s.locale,s.status,c.raw_text,c.source_path,c.pair_ordinal FROM text_slot s LEFT JOIN text_candidate c ON c.slot_id=s.id WHERE s.row_id=? ORDER BY s.id,c.id', (evidence_id,))]
                result = {'container': dict(row), 'text_candidates': texts}
        elif family == 'quest_review_archive':
            row = self.db[family].execute('SELECT * FROM evidence_row WHERE id=?', (evidence_id,)).fetchone()
            if row is None:
                raise KeyError(ref)
            raw_row = json.loads((self.source / row['source_path']).read_bytes().decode('utf-8-sig'))[row['source_ordinal']]
            if json.loads(row['raw_json']) != raw_row or hashlib.sha256(row['raw_json'].encode()).hexdigest() != row['raw_sha256']:
                raise ValueError('Review row source recheck')
            texts = [dict(x) for x in self.db[family].execute('SELECT s.owner_field,s.locale,s.status,c.raw_text,c.source_path,c.pair_ordinal FROM text_slot s LEFT JOIN text_candidate c ON c.slot_id=s.id WHERE s.row_id=? ORDER BY s.id,c.id', (evidence_id,))]
            result = {'container': dict(row), 'text_candidates': texts}
        elif family == 'codexquest_archive':
            row = self.db[family].execute('SELECT id,run_id,source_path,git_blob_oid,raw_sha256,shape,native_id,section_state,section_count,semantic_mapping FROM codex_root WHERE id=?', (evidence_id,)).fetchone()
            if row is None:
                child = self.db[family].execute('SELECT * FROM codex_child WHERE id=?', (evidence_id,)).fetchone()
                if child is None:
                    raise KeyError(ref)
                root_id = child['root_id']
                root = self.db[family].execute('SELECT source_path,raw_bytes FROM codex_root WHERE id=?', (child['root_id'],)).fetchone()
                raw = (self.source / root['source_path']).read_bytes()
                if raw != root['raw_bytes'] or _pointer(json.loads(raw.decode('utf-8-sig')), child['source_pointer']) != json.loads(child['raw_json']):
                    raise ValueError('Codex child exact source recheck')
                container = {**dict(child), 'source_path': root['source_path']}
                if len(container['raw_json'].encode()) > 262144:
                    container['raw_json'] = None
                    container['raw_payload_partial'] = True
                result = {'container': container, 'single_physical_child': True}
                text_scope = child['source_pointer'].rstrip('/') + '/'
            else:
                root_id = evidence_id
                raw = (self.source / row['source_path']).read_bytes()
                if hashlib.sha256(raw).hexdigest() != row['raw_sha256']:
                    raise ValueError('Codex root exact source recheck')
                children = [dict(x) for x in self.db[family].execute('SELECT id,kind,source_pointer,physical_ordinal,raw_sha256 FROM codex_child WHERE root_id=? ORDER BY source_pointer,id LIMIT ?', (evidence_id, child_limit + 1))]
                result = {'container': dict(row), 'children': children[:child_limit], 'children_truncated': len(children) > child_limit,
                          'raw_source_bytes': raw.decode('utf-8-sig') if len(raw) <= 262144 else None,
                          'raw_payload_partial': len(raw) > 262144}
                text_scope = None
            # Bounded read-through of sealed physical TextMap occurrences. A
            # new-shape wrapper's semantic text role remains unresolved; this
            # does not turn a section-like child into a historical node.
            slot_sql = ('SELECT * FROM codex_text_slot WHERE root_id=? AND locale=?' +
                        (' AND substr(source_pointer,1,length(?))=?' if text_scope is not None else '') +
                        ' ORDER BY source_pointer,id LIMIT 13')
            slot_args = (root_id, 'CHS', text_scope, text_scope) if text_scope is not None else (root_id, 'CHS')
            text_slots = [dict(s) for s in self.db[family].execute(slot_sql, slot_args)]
            result['text_slots_truncated'] = len(text_slots) > 12
            result['text_slots'] = []
            result['text_candidates'] = []
            for slot in text_slots[:12]:
                result['text_slots'].append({'id': slot['id'], 'locale': slot['locale'],
                    'owner_field': slot['source_label'], 'source_pointer': slot['source_pointer'],
                    'status': slot['status'], 'candidate_count': slot['candidate_count'],
                    'owner_role_status': slot['semantic_owner_status'],
                    'semantic_owner_mapping': 'unresolved',
                    'field_continuity_status': 'unknown'})
                result['text_candidates'].extend(dict(c) for c in self.db[family].execute(
                    'SELECT * FROM codex_text_candidate WHERE slot_id=? ORDER BY id', (slot['id'],)))
        elif family == 'quest_talk_physical':
            row = self.quest_talk.execute('SELECT * FROM physical_container WHERE id=?', (evidence_id,)).fetchone()
            if row is None:
                raise KeyError(ref)
            raw = (self.source / row['source_path']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != row['raw_sha256']:
                raise ValueError('Quest/Talk exact source recheck')
            result = {'container': dict(row), 'raw_source_bytes': raw.decode('utf-8-sig') if len(raw) <= 262144 else None, 'raw_payload_partial': len(raw) > 262144}
            slots = self.quest_talk.execute(
                'SELECT s.*,n.ordinal,n.text_mapping FROM text_slot s JOIN talk_node n ON n.id=s.node_id '
                'WHERE n.container_id=? AND s.locale=? ORDER BY n.ordinal,s.id LIMIT 13',
                (evidence_id, 'CHS')).fetchall()
            result['text_slots_truncated'] = len(slots) > 12
            result['text_slots'] = []
            result['text_candidates'] = []
            for slot in slots[:12]:
                locators = json.loads(slot['locators_json'])
                result['text_slots'].append({'id': slot['id'], 'locale': slot['locale'],
                    'owner_field': 'talk_node_text', 'status': slot['status'],
                    'candidate_count': len(locators), 'owner_role_status': slot['text_mapping'],
                    'semantic_owner_mapping': 'physical_text_slot',
                    'field_continuity_status': 'unknown'})
                for locator in locators:
                    if locator['raw_text'] != slot['raw_text']:
                        raise ValueError('Talk text locator/raw text mismatch')
                    result['text_candidates'].append({'slot_id': slot['id'],
                        'source_path': locator['path'], 'pointer': locator['pointer'],
                        'pair_ordinal': locator['pair_ordinal'], 'raw_text': locator['raw_text'],
                        'locale': slot['locale'], 'tier': locator['tier']})
        else:
            raise ValueError('unsupported family')
        return {'evidence_ref': ref, 'snapshot': SNAPSHOT, 'partial': True, 'product_accepted': False,
                'semantic_mapping': self._semantic(family, evidence_id), **result}

    def inspect(self, ref: dict) -> dict:
        value = self.read(ref, child_limit=0)
        return {'evidence_ref': ref, 'snapshot': SNAPSHOT,
                'source_path': value['container'].get('source_path', value['container'].get('quest_source_path')),
                'pointer': value['container'].get('pointer', value['container'].get('source_pointer')),
                'parser_run': ref['run'], 'semantic_mapping': value['semantic_mapping'], 'product_accepted': False,
                'partial': True, 'raw_sha256': value['container'].get('raw_sha256')}

    def follow(self, ref: dict) -> dict:
        family, run, evidence_id = ref['family'], ref['run'], ref['id']
        self._scope(family, run)
        edges = []
        if family == 'quest_review_archive':
            sql = '''SELECT l.role,l.status,l.search_complete,l.candidate_count,c.* FROM bound_locator l
                     LEFT JOIN bound_candidate c ON c.locator_id=l.id WHERE l.source_review_row_id=? ORDER BY l.id,c.id'''
            for row in self.binding.execute(sql, (evidence_id,)):
                edge = dict(row)
                edge['candidate_navigation'] = True
                edge['logical_relation_closure'] = False
                if edge.get('target_occurrence_id'):
                    target = {'family': edge['target_family'], 'run': edge['target_run'], 'id': edge['target_occurrence_id']}
                    inspected = self.inspect(target)
                    if inspected['source_path'] != edge['target_source_path'] or inspected['pointer'] != edge['target_source_pointer'] or inspected['raw_sha256'] != edge['target_raw_sha256']:
                        raise ValueError('Review second-hop exact target drift')
                    edge['target_ref'] = target
                edges.append(edge)
        elif family == 'quest_narrative_archive':
            child = self.db[family].execute('SELECT * FROM child_observation WHERE id=?', (evidence_id,)).fetchone()
            if child is not None:
                for row in self.db[family].execute('SELECT * FROM child_candidate WHERE observation_id=? ORDER BY id', (evidence_id,)):
                    edge = dict(row)
                    edge['target_ref'] = {'family': family, 'run': run, 'id': row['target_row_id']}
                    edge['candidate_navigation'] = True
                    edge['logical_relation_closure'] = False
                    edge['semantic_mapping'] = 'versioned_physical_locator_only'
                    edges.append(edge)
                return {'evidence_ref': ref, 'edges': edges, 'partial': True, 'product_accepted': False}
            for row in self.db[family].execute('SELECT * FROM excel_candidate WHERE source_row_id=? ORDER BY id', (evidence_id,)):
                edge = dict(row)
                edge['target_ref'] = {'family': family, 'run': run, 'id': row['target_main_row_id']}
                edge['candidate_navigation'] = True
                edge['logical_relation_closure'] = False
                edges.append(edge)
            for row in self.db[family].execute('SELECT * FROM main_root_candidate WHERE main_row_id=? ORDER BY id', (evidence_id,)):
                edge = dict(row)
                edge['target_ref'] = {'family': 'quest_talk_physical', 'run': self.runs['quest_talk_physical'], 'id': row['quest_talk_container_id']}
                edge['candidate_navigation'] = True
                edge['logical_relation_closure'] = False
                edges.append(edge)
        elif family == 'quest_talk_physical':
            for row in self.quest_talk.execute('SELECT * FROM physical_reference WHERE container_id=? ORDER BY id', (evidence_id,)):
                edge = dict(row)
                edge['candidates'] = json.loads(row['candidates_json'])
                edge['candidate_navigation'] = True
                edge['logical_relation_closure'] = False
                if not edge['candidates']:
                    edges.append(edge)
                for candidate in edge['candidates']:
                    target = self.quest_talk.execute(
                        'SELECT source_path,pointer,kind,raw_sha256,ingest_run_id FROM physical_container WHERE id=?',
                        (candidate['container_id'],)).fetchone()
                    if (target is None or target['source_path'] != candidate['path']
                            or target['pointer'] != candidate['pointer']
                            or target['kind'] != candidate['kind']
                            or target['ingest_run_id'] != run
                            or _sha(self.source / target['source_path']) != target['raw_sha256']):
                        raise ValueError('Quest/Talk physical candidate second-hop drift')
                    edges.append({**edge, 'candidate': candidate,
                        'target_ref': {'family': family, 'run': run, 'id': candidate['container_id']}})
            for row in self.db['quest_narrative_archive'].execute('SELECT id,source_pointer,physical_ordinal,semantic_order_status FROM child_observation WHERE quest_talk_container_id=? ORDER BY physical_ordinal,id', (evidence_id,)):
                edges.append({'edge': 'physical_child_observation', 'target_ref': {'family': 'quest_narrative_archive', 'run': self.runs['quest_narrative_archive'], 'id': row['id']},
                              'source_pointer': row['source_pointer'], 'physical_ordinal': row['physical_ordinal'],
                              'semantic_order_status': row['semantic_order_status'], 'candidate_navigation': True,
                              'logical_relation_closure': False})
        elif family == 'codexquest_archive':
            child = self.db[family].execute('SELECT root_id,kind,source_pointer FROM codex_child WHERE id=?', (evidence_id,)).fetchone()
            root_id = child['root_id'] if child else evidence_id
            root = self.db[family].execute('SELECT source_path,shape,raw_bytes FROM codex_root WHERE id=?', (root_id,)).fetchone()
            if root is None:
                raise KeyError(ref)
            raw = (self.source / root['source_path']).read_bytes()
            if raw != root['raw_bytes']:
                raise ValueError('Codex source bytes differ from sealed foundation')
            source_root = json.loads(raw.decode('utf-8-sig'))
            sections_field, nodes_field = OLD_CODEX.get(root['shape'], ('JIJKODHIEED', 'DCBDMJAPBOK'))
            sections = source_root.get(sections_field)
            if root['shape'] == 'new_metadata_only':
                if sections_field in source_root:
                    raise ValueError('metadata-only source gained section field')
                sections = []
            elif not isinstance(sections, list):
                raise ValueError('Codex section structure changed')
            if child is None:
                for ordinal, section in enumerate(sections):
                    pointer = f'/{sections_field}/{ordinal}'
                    matches = self.db[family].execute('SELECT id,kind,source_pointer,raw_json FROM codex_child WHERE root_id=? AND source_pointer=?', (root_id, pointer)).fetchall()
                    if len(matches) != 1 or matches[0]['kind'] != 'section_like' or json.loads(matches[0]['raw_json']) != section:
                        raise ValueError('Codex section source-to-foundation mismatch')
                    edges.append({'edge': 'physical_section', 'target_ref': {'family': family, 'run': run, 'id': matches[0]['id']},
                                  'physical_ordinal': ordinal, 'semantic_mapping': 'historical_equivalent' if root['shape'] in OLD_CODEX else 'versioned_physical_section',
                                  'candidate_navigation': True, 'logical_relation_closure': False})
            elif child['kind'] == 'section_like':
                parts = child['source_pointer'].strip('/').split('/')
                if len(parts) != 2 or parts[0] != sections_field or not parts[1].isdigit() or int(parts[1]) >= len(sections):
                    raise ValueError('Codex section coordinate')
                section = sections[int(parts[1])]
                if not isinstance(section, dict):
                    raise ValueError('Codex section type')
                nodes = section.get(nodes_field)
                if nodes is None and root['shape'] not in OLD_CODEX:
                    if nodes_field in section:
                        raise ValueError('null Codex node array')
                    edges.append({'edge': 'source_authored_leaf_section', 'semantic_mapping': 'versioned_leaf_section',
                                  'candidate_navigation': False, 'logical_relation_closure': False})
                elif not isinstance(nodes, list):
                    raise ValueError('Codex node array shape')
                else:
                    for ordinal, node in enumerate(nodes):
                        pointer = child['source_pointer'] + f'/{nodes_field}/{ordinal}'
                        matches = self.db[family].execute('SELECT id,kind,source_pointer,raw_json FROM codex_child WHERE root_id=? AND source_pointer=?', (root_id, pointer)).fetchall()
                        if len(matches) != 1 or matches[0]['kind'] != 'node_like' or json.loads(matches[0]['raw_json']) != node:
                            raise ValueError('Codex node source-to-foundation mismatch')
                        edges.append({'edge': 'physical_node', 'target_ref': {'family': family, 'run': run, 'id': matches[0]['id']},
                                      'physical_ordinal': ordinal, 'semantic_mapping': 'historical_equivalent' if root['shape'] in OLD_CODEX else 'versioned_physical_node',
                                      'candidate_navigation': True, 'logical_relation_closure': False})
            for row in self.db[family].execute('SELECT * FROM codex_navigation WHERE root_id=? ORDER BY id', (root_id,)) if child is None else ():
                edge = dict(row)
                edge['candidates'] = [dict(x) for x in self.db[family].execute('SELECT * FROM codex_candidate WHERE navigation_id=? ORDER BY id', (row['id'],))]
                edge['candidate_navigation'] = True
                edge['logical_relation_closure'] = False
                edge['semantic_mapping'] = 'mapping_unresolved'
                edge['physical_candidate_scan_complete'] = bool(row['search_complete'])
                edges.append(edge)
        return {'evidence_ref': ref, 'edges': edges, 'partial': True, 'product_accepted': False}
