"""Exact-run bounded 7.1 adapter for historical Event component-10 only."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .event_narrative_retrieval import EventNarrativeProvider, _fp
from .family_evidence import FamilyEvidenceResult

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / 'schemas/event-successor-7.1-v1.sql'


class EventLegacy71Provider(EventNarrativeProvider):
    def __init__(self, artifact, runtime_manifest):
        self.path = Path(artifact).resolve()
        self.manifest_path = Path(runtime_manifest).resolve()
        self.invalidated = False
        mf = _fp(self.manifest_path)
        af = _fp(self.path)
        manifest = json.loads(self.manifest_path.read_text())
        scope = (ROOT / manifest['scope_manifest']).resolve()
        sf = _fp(scope)
        if (manifest['contract'] != 'event-narrative-runtime-v1'
                or manifest['family'] != self.family
                or manifest['family_contract_version'] != 'event-narrative-evidence-v1'
                or manifest['product_accepted'] is not False
                or manifest['artifact'] != {'size_bytes': af[2], 'sha256': af[4]}
                or manifest['release']['scope_sha256'] != sf[4]
                or manifest['release']['snapshot_id'] !=
                'anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'):
            raise ValueError('Event component-10 exact 7.1 binding')
        self.manifest = manifest
        self.family_contract_version = manifest['family_contract_version']
        self.coverage_label = 'complete selected historical Event component-10 physical/text boundary'
        self.scope = scope
        self.run = manifest['release']['run_id']
        self.bindings = {self.path: af[:4], self.manifest_path: mf[:4], scope: sf[:4]}
        self.conn = sqlite3.connect(self.path.as_uri() + '?mode=ro&immutable=1',
                                    uri=True, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        reference = sqlite3.connect(':memory:')
        reference.executescript(SCHEMA.read_text())
        expected = [list(row) for row in reference.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
        reference.close()
        structure = [list(row) for row in self.conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
        release = self.conn.execute(
            'SELECT manifest_json FROM event_narrative_release WHERE id=1').fetchone()
        if (structure != expected or release is None
                or json.loads(release[0]) != manifest['release']
                or self.conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                or self.conn.execute('PRAGMA foreign_key_check').fetchall()):
            raise ValueError('Event component-10 artifact qualification')
        for table, count in manifest['counts'].items():
            if self.conn.execute('SELECT count(*) FROM ' + table).fetchone()[0] != count:
                raise ValueError('Event component-10 count seal')
        if any(Path(str(self.path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
            raise ValueError('Event component-10 SQLite sidecar')
        declarations = json.loads(scope.read_text())['field_continuity']
        aliases = {}
        for mapping in declarations.values():
            for old, proof in mapping.items():
                if old in aliases and aliases[old] != proof['new_field']:
                    raise ValueError('Event component-10 ambiguous historical field alias')
                aliases[old] = proof['new_field']
        self.owner_aliases = aliases
        self.historical_by_physical = {new: old for old, new in aliases.items()}
        if len(self.historical_by_physical) != len(aliases):
            raise ValueError('Event component-10 non-injective owner aliases')

    def _with_aliases(self, result):
        """Add compatibility metadata; never rewrite the physical owner field."""
        if result.status != 'ok':
            return result
        data = dict(result.data)
        for key in ('items', 'text_slots'):
            if key in data:
                data[key] = [dict(item) for item in data[key]]
                for item in data[key]:
                    old = self.historical_by_physical.get(item.get('owner_field'))
                    if old is not None:
                        item['historical_owner_field'] = old
        return FamilyEvidenceResult(result.status, result.family, result.ingest_run_id,
                                    result.family_contract_version, result.coverage,
                                    result.corpus_complete, data)

    def capability(self):
        result = super().capability()
        result['coverage_semantics']['full_7_1'] = False
        result['coverage_semantics']['standalone_candidate_only'] = True
        result['owner_field_versioned_aliases'] = self.owner_aliases
        result['owner_field_response_semantics'] = (
            'owner_field is the exact 7.1 physical field; historical_owner_field is '
            'an optional derived compatibility alias, never source provenance')
        return result

    def get_evidence(self, *, ingest_run_id, family_contract_version, occurrence_id):
        return self._with_aliases(super().get_evidence(
            ingest_run_id=ingest_run_id,
            family_contract_version=family_contract_version,
            occurrence_id=occurrence_id))

    def search_text(self, *, ingest_run_id, family_contract_version, query,
                    filters=None, cursor=None, limit=50):
        translated = dict(filters or {})
        if 'owner_field' in translated:
            translated['owner_field'] = self.owner_aliases.get(
                translated['owner_field'], translated['owner_field'])
        return self._with_aliases(super().search_text(
            ingest_run_id=ingest_run_id, family_contract_version=family_contract_version,
            query=query, filters=translated, cursor=cursor, limit=limit))
