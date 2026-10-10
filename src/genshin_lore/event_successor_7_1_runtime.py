"""Prepublication, exact-run component-11 Event v2 read-through adapter.

This deliberately is not registered in the accepted 7.0 product or MCP surface.
Historical owner-field filters are translated only by the six source-proven raw
locator renames; neither the old nor new obfuscated field gains a semantic name.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .event_narrative_retrieval import EventNarrativeProvider, _fp

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / 'schemas/event-successor-7.1-v1.sql'


class EventSuccessor71Provider(EventNarrativeProvider):
    def __init__(self, artifact, runtime_manifest):
        self.path = Path(artifact).resolve()
        self.manifest_path = Path(runtime_manifest).resolve()
        self.invalidated = False
        manifest_fingerprint = _fp(self.manifest_path)
        artifact_fingerprint = _fp(self.path)
        manifest = json.loads(self.manifest_path.read_text())
        scope = (ROOT / manifest['scope_manifest']).resolve()
        scope_fingerprint = _fp(scope)
        if (manifest['contract'] != 'event-narrative-runtime-v2'
                or manifest['family'] != self.family
                or manifest['family_contract_version'] != 'event-narrative-evidence-v2'
                or manifest['product_accepted'] is not False
                or manifest['artifact'] != {'size_bytes': artifact_fingerprint[2],
                                           'sha256': artifact_fingerprint[4]}
                or manifest['release']['scope_sha256'] != scope_fingerprint[4]
                or manifest['release']['snapshot_id'] !=
                'anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'):
            raise ValueError('Event 7.1 standalone exact binding')
        self.manifest = manifest
        self.family_contract_version = manifest['family_contract_version']
        self.coverage_label = 'complete selected Tanuki Travel and AD Shooting physical/text boundary'
        self.scope = scope
        self.run = manifest['release']['run_id']
        self.bindings = {self.path: artifact_fingerprint[:4],
                         self.manifest_path: manifest_fingerprint[:4],
                         scope: scope_fingerprint[:4]}
        self.conn = sqlite3.connect(self.path.as_uri() + '?mode=ro&immutable=1',
                                    uri=True, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        reference = sqlite3.connect(':memory:')
        reference.executescript(SCHEMA.read_text())
        expected_structure = [list(row) for row in reference.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
        reference.close()
        structure = [list(row) for row in self.conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
        release = self.conn.execute(
            'SELECT manifest_json FROM event_narrative_release WHERE id=1').fetchone()
        if (structure != expected_structure or release is None
                or json.loads(release[0]) != manifest['release']
                or self.conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                or self.conn.execute('PRAGMA foreign_key_check').fetchall()):
            raise ValueError('Event 7.1 artifact qualification')
        for table, count in manifest['counts'].items():
            if self.conn.execute('SELECT count(*) FROM ' + table).fetchone()[0] != count:
                raise ValueError('Event 7.1 count seal')
        if any(Path(str(self.path) + suffix).exists()
               for suffix in ('-wal', '-shm', '-journal')):
            raise ValueError('Event 7.1 SQLite sidecar')
        scope_data = json.loads(scope.read_text())
        self.owner_aliases = {old: item['new_field']
                              for old, item in scope_data['field_continuity'].items()}

    def capability(self):
        result = super().capability()
        result['coverage_semantics']['full_7_1'] = False
        result['coverage_semantics']['standalone_candidate_only'] = True
        result['owner_field_versioned_aliases'] = self.owner_aliases
        return result

    def search_text(self, *, ingest_run_id, family_contract_version, query,
                    filters=None, cursor=None, limit=50):
        translated = dict(filters or {})
        if 'owner_field' in translated:
            translated['owner_field'] = self.owner_aliases.get(
                translated['owner_field'], translated['owner_field'])
        return super().search_text(ingest_run_id=ingest_run_id,
                                   family_contract_version=family_contract_version,
                                   query=query, filters=translated,
                                   cursor=cursor, limit=limit)
