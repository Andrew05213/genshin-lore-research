"""Bounded, exact-run Filmfest 7.1 read-through; never registered as product."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .event_narrative_retrieval import EventNarrativeProvider, _fp

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / 'schemas/event-filmfest-7.1-v1.sql'
SNAPSHOT = 'anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'


class EventFilmfest71Provider(EventNarrativeProvider):
    def __init__(self, artifact, runtime_manifest):
        self.path = Path(artifact).resolve()
        self.manifest_path = Path(runtime_manifest).resolve()
        self.invalidated = False
        manifest_fingerprint = _fp(self.manifest_path)
        artifact_fingerprint = _fp(self.path)
        manifest = json.loads(self.manifest_path.read_text())
        scope = (ROOT / manifest['scope_manifest']).resolve()
        scope_fingerprint = _fp(scope)
        if (manifest['contract'] != 'event-narrative-runtime-v3'
                or manifest['family'] != self.family
                or manifest['family_contract_version'] != 'event-narrative-evidence-v3'
                or manifest['product_accepted'] is not False
                or manifest['artifact'] != {'size_bytes': artifact_fingerprint[2],
                                           'sha256': artifact_fingerprint[4]}
                or manifest['release']['scope_sha256'] != scope_fingerprint[4]
                or manifest['release']['snapshot_id'] != SNAPSHOT):
            raise ValueError('Filmfest 7.1 standalone exact binding')
        self.manifest = manifest
        self.family_contract_version = manifest['family_contract_version']
        self.coverage_label = 'complete selected Filmfest physical/text boundary'
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
            raise ValueError('Filmfest 7.1 artifact qualification')
        for table, count in manifest['counts'].items():
            if self.conn.execute('SELECT count(*) FROM ' + table).fetchone()[0] != count:
                raise ValueError('Filmfest 7.1 count seal')
        if any(Path(str(self.path) + suffix).exists()
               for suffix in ('-wal', '-shm', '-journal')):
            raise ValueError('Filmfest 7.1 SQLite sidecar')
        scope_data = json.loads(scope.read_text())
        owner_fields = {owner['field'] for spec in scope_data['sources']
                        for owner in spec['text_owners']}
        self.owner_aliases = {
            old: proof['new_field'] for family in scope_data['field_continuity'].values()
            for old, proof in family.items() if proof['new_field'] in owner_fields}

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
            old = translated['owner_field']
            base, slash, suffix = old.partition('/')
            translated['owner_field'] = self.owner_aliases.get(base, base) + (
                slash + suffix if slash else '')
        return super().search_text(ingest_run_id=ingest_run_id,
                                   family_contract_version=family_contract_version,
                                   query=query, filters=translated,
                                   cursor=cursor, limit=limit)
