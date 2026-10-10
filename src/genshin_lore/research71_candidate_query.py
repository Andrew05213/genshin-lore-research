"""Prepublication 7.1 read-through adapters on the existing Research API.

Production qualification still uses current accepted 7.0. This subclass only
handles declared 7.1 storage keys and exposes the original proof restrictions.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .research_query import ResearchQuery, ResearchUnavailable
from .research_refs import decode_ref
from .research71_candidate_adapters import fetch, canonical, text_proof, SPECS
from .research_index import sha256_file
from .research_adapters import ROW_FAMILIES


class CandidateResearch71(ResearchQuery):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        profile_path=kwargs.get('profile_path',args[3] if len(args)>3 else None)
        profile=json.loads(Path(profile_path).read_text())
        if not kwargs.get('allow_prepublication_profile') or profile.get('qualification_scope')!='local_7.1_candidate_only' or profile.get('product_accepted') is not False:
            self.close();raise ResearchUnavailable('candidate is not a production/accepted Research profile')
        if profile.get('candidate_runtime_sha256')!=sha256_file(Path(__file__)):
            self.close();raise ResearchUnavailable('candidate runtime implementation drift')
        if self.manifest.get('candidate_adapter_revision')!='research71-explicit-physical-adapters-v1' or self.manifest['snapshot']['version']!='7.1':
            self.close();raise ResearchUnavailable('explicit 7.1 candidate adapter revision required')
        if self.unavailable:
            self.close();raise ResearchUnavailable('candidate exact dependencies unavailable: '+','.join(self.unavailable))
        for name,seal in self.manifest['adapter_implementation_sha256'].items():
            if sha256_file(Path(__file__).parent/name)!=seal:
                self.close();raise ResearchUnavailable('candidate storage adapter drift: '+name)

    def _custom(self, row):
        return self.components[row['source_key']]['adapter'] not in {'core33','formal_compatible','talk_physical'}

    def _recheck_container(self, row):
        if not self._custom(row):
            return super()._recheck_container(row)
        src=self._source(row['source_key']);got=fetch(src,row['source_table'],row['pk_column'],row['pk_value'])
        if got is None:raise ResearchUnavailable('candidate physical container disappeared')
        got=dict(got)
        path=got.get('source_path');pointer=got.get('pointer',got.get('source_pointer','/'))
        if (path,pointer)!=(row['source_path'],row['source_pointer']):
            raise ResearchUnavailable('candidate container physical coordinates drift')
        if self.r0_provenance.get(path)!=row['source_file_sha256']:
            raise ResearchUnavailable('candidate container snapshot-local source seal drift')
        for column,expected in [('parser_build_id',row['evidence_parser']),('ingest_run_id',row['evidence_run']),('run_id',row['evidence_run'])]:
            if column in got and got[column]!=expected:raise ResearchUnavailable('candidate physical run/parser drift')
        return got

    def _recheck_text(self, row):
        if not self._custom(row):
            return super()._recheck_text(row)
        container=self.db.execute('SELECT * FROM research_container WHERE id=?',(row['container_id'],)).fetchone()
        self._recheck_container(container)
        src=self._source(row['source_key']);got=fetch(src,row['source_table'],row['pk_column'],row['pk_value'])
        if got is None:raise ResearchUnavailable('candidate text disappeared')
        got=dict(got)
        raw=got.get('raw_text')
        if raw!=row['raw_text'] or hashlib.sha256(raw.encode()).hexdigest()!=row['text_sha256']:
            raise ResearchUnavailable('candidate text content/fulltext SHA drift')
        name=self.components[row['source_key']]['adapter']
        if name=='quest_talk_physical':
            loc=json.loads(got['locators_json'])
            if len(loc)!=1 or (loc[0]['path'],loc[0]['pointer'],loc[0]['pair_ordinal'],loc[0]['raw_text'])!=(row['text_source_path'],row['text_source_pointer'],row['physical_pair_ordinal'],raw):
                raise ResearchUnavailable('Talk physical text locator drift')
            node=src.execute('SELECT * FROM talk_node WHERE id=?',(got['node_id'],)).fetchone()
            if node['container_id']!=container['occurrence_id']:raise ResearchUnavailable('Talk text parent drift')
        elif name=='character_text':
            resolution=src.execute('SELECT * FROM resolution WHERE owner_id=? AND locale=?',(got['owner_id'],got['locale'])).fetchone()
            if resolution is None or resolution['row_id']!=container['occurrence_id'] or resolution['field_name']!=row['owner_field']:
                raise ResearchUnavailable('Character text owner/parent drift')
            if ('/'+got['source_key'],got['physical_ordinal'])!=(row['text_source_pointer'],row['physical_pair_ordinal']):raise ResearchUnavailable('Character text physical pair drift')
        elif name.startswith('narrative_'):
            if str(got['row_ordinal'])!=container['pk_value'] or got['field']!=row['owner_field'] or '/'+str(got['hash'])!=row['text_source_pointer']:
                raise ResearchUnavailable('narrative text parent/field/pointer drift')
        elif 'slot_id' in got:
            slot='codex_text_slot' if name=='codexquest_archive' else 'native71_slot' if name=='native_message' else 'event71_slot' if name=='event_native_text_17' else 'text_slot'
            owner=src.execute(f'SELECT * FROM "{slot}" WHERE id=?',(got['slot_id'],)).fetchone()
            if owner is None or owner['root_id' if name=='codexquest_archive' else 'row_id']!=container['occurrence_id'] or owner['locale']!=row['locale']:
                raise ResearchUnavailable('candidate text slot parent/locale drift')
        for col,expected in [('source_path',row['text_source_path']),('source_pointer',row['text_source_pointer']),('pointer',row['text_source_pointer']),('pair_ordinal',row['physical_pair_ordinal']),('locale',row['locale'])]:
            if col in got and got[col]!=expected:raise ResearchUnavailable('candidate text physical coordinate drift: '+col)
        if self.r0_provenance.get(row['text_source_path'])!=row['text_source_file_sha256']:
            raise ResearchUnavailable('candidate text source seal drift')
        return raw

    def _proof(self, row):
        mode=self.components[row['source_key']]['adapter'];src=self._source(row['source_key'])
        if mode in SPECS:
            result=text_proof(src,mode,row)
            candidate=dict(fetch(src,row['source_table'],row['pk_column'],row['pk_value']))
            inherited={}
            if 'slot_id' in candidate:
                table='codex_text_slot' if mode=='codexquest_archive' else 'native71_slot' if mode=='native_message' else 'event71_slot' if mode=='event_native_text_17' else 'text_slot'
                inherited=dict(src.execute(f'SELECT * FROM "{table}" WHERE id=?',(candidate['slot_id'],)).fetchone())
            inherited.update(candidate)
            # Preserve explicit source proof atoms; cardinality and an accepted
            # artifact never substitute for owner/continuity proof.
            if mode in {'native_successor_physical','native_high_value_physical'}:
                # Their accepted physical runtime explicitly carries this
                # payload-level limitation, even for a unique text candidate.
                result['semantic_owner_mapping']='unresolved'
            elif mode not in {'narrative_reminder','event_physical_13'}:
                result['semantic_owner_mapping']=inherited.get('semantic_owner_mapping','unknown')
            if inherited.get('semantic_owner_mapping_unresolved') in (True,1):
                result['semantic_owner_mapping']='unresolved'
            result['resolution_status']=inherited.get('resolution_status',result['resolution_status'])
            return result
        if mode=='character_text':
            candidate=fetch(src,row['source_table'],row['pk_column'],row['pk_value'])
            resolution=dict(src.execute('SELECT * FROM resolution WHERE owner_id=? AND locale=?',(candidate['owner_id'],candidate['locale'])).fetchone())
            return {'resolution_status':resolution['status'],'candidate_count':resolution['candidate_count'],'locale':resolution['locale'],'owner_role_status':'unknown','semantic_owner_mapping':'unknown','relation_status':'physical_candidate','logical_identity_proven':False}
        candidate=dict(fetch(src,row['source_table'],row['pk_column'],row['pk_value']));proof=dict(candidate)
        family=self.components[row['source_key']]['family']
        if mode=='formal_compatible' and 'slot_id' in candidate:
            slot=ROW_FAMILIES[family]['slot']
            proof={**dict(src.execute(f'SELECT * FROM "{slot}" WHERE id=?',(candidate['slot_id'],)).fetchone()),**proof}
        elif mode=='core33':
            prefix=row['source_table'].removesuffix('_text_candidate_occurrence')
            columns={r[1] for r in src.execute('PRAGMA table_info('+prefix+'_text_resolution_occurrence)')}
            if 'resolution_occurrence_id' in candidate:
                pk='id' if 'id' in columns else 'occurrence_id'
                proof={**dict(src.execute(f'SELECT * FROM {prefix}_text_resolution_occurrence WHERE {pk}=?',(candidate['resolution_occurrence_id'],)).fetchone()),**proof}
            elif 'owner_id' in candidate:
                proof={**dict(src.execute(f'SELECT * FROM {prefix}_text_resolution_occurrence WHERE owner_id=? AND ingest_run_id=? AND locale=?',(candidate['owner_id'],candidate['ingest_run_id'],candidate['locale'])).fetchone()),**proof}
        return {'resolution_status':proof.get('resolution_status',proof.get('status','unknown')),'candidate_count':proof.get('candidate_count'),'selected':proof.get('selected'),'selected_candidate_count':proof.get('selected_candidate_count'),'locale':proof.get('locale',row['locale']),'owner_role_status':proof.get('owner_role_status','unknown'),'semantic_owner_mapping':proof.get('semantic_owner_mapping','unknown'),'field_continuity_status':proof.get('field_continuity_status','unknown'),'relation_status':'physical_candidate','logical_identity_proven':False}

    def _decorate_context(self,item,row,container=None):
        result=super()._decorate_context(item,row,container)
        if row is not None:
            result['text_proof']=self._proof(row)
        key=(container or row)['source_key'] if container is not None or row is not None else None
        if key:result['capability_limitations']=self.components[key].get('limitations',[])
        result['product_accepted']=False;result['full_research_7_1_accepted']=False
        return result

    def read(self,*args,**kwargs):
        value=super().read(*args,**kwargs)
        for item in value.get('context',[]):
            row=self._text_for(decode_ref(item['evidence_ref']));item['text_proof']=self._proof(row)
        return value

    def _recheck_relation(self,r):
        if not r['adapter_id'].startswith(('character_foundation:','quest_review_binding:','native_message:','event_native_text_17:','native_successor_physical:','native_high_value_physical:','codexquest_archive:','narrative_','talk_physical:')):
            return super()._recheck_relation(r)
        self._recheck_container(self.db.execute('SELECT * FROM research_container WHERE id=?',(r['container_id'],)).fetchone())
        got=fetch(self._source(r['source_key']),r['source_table'],r['pk_column'],r['pk_value'])
        if got is None or canonical(dict(got))!=r['raw_locator']:
            raise ResearchUnavailable('whole source physical relation row drift')
        source=dict(got)
        if source.get('status',source.get('state','mapping_unresolved'))!=r['status'] or source.get('candidate_count',source.get('known_candidate_count',r['candidate_count']))!=r['candidate_count']:
            raise ResearchUnavailable('physical relation state/count drift')

    def _recheck_candidate(self,relation,c):
        if relation['raw_locator'] and relation['raw_locator'].startswith('{') and c['raw_target'] and c['raw_target'].startswith('{'):
            got=fetch(self._source(relation['source_key']),c['source_table'],c['pk_column'],c['pk_value'])
            if got is None or canonical(dict(got))!=c['raw_target']:
                raise ResearchUnavailable('whole physical target candidate drift')
            return
        return super()._recheck_candidate(relation,c)

    def _target_ref(self,c):
        # Explicit transport aliases for the sealed 7.1 narrative/QT inputs.
        # The original candidate fields remain unchanged in index and output.
        aliases={'npc':'npc_archive','dialog':'dialog_archive','reminder':'reminder_archive','quest_talk':'quest_talk_pilot'}
        family=aliases.get(c['target_family'],c['target_family'])
        c=dict(c)
        if c.get('source_table')=='document_candidate' and family=='readable_archive' and c.get('target_pointer')=='':
            # The accepted Document file-locator adapter stores an empty file
            # root. Research's Readable container view explicitly serializes
            # that file root as '/'. This is not a JSON-record pointer repair.
            c['target_pointer']='/'
        # The accepted NPC/Dialog/Reminder contract defines a specific trio
        # of target artifacts. A coordinate in that declared namespace must
        # not be confused with a same-coordinate core projection. Preserve
        # the original index fields; constrain only this read-through lookup.
        if c.get('source_table')=='physical_candidate' and c.get('relation_id') is not None:
            relation=self.db.execute('SELECT source_key,raw_locator FROM research_relation WHERE id=?',(c['relation_id'],)).fetchone()
            role={'npc_archive':'npc','dialog_archive':'dialog','reminder_archive':'reminder'}.get(family)
            if role and relation and relation['source_key'].startswith('7.1-narrative_'):
                raw=json.loads(relation['raw_locator'])
                if raw.get('target_family')!=role:return None
                namespace=self.components['7.1-narrative_'+role]
                for field,key in [('target_contract','contract'),('target_parser','parser'),('target_run','run')]:
                    if c.get(field) is not None and c[field]!=namespace[key]:return None
                    c[field]=namespace[key]
        conditions=[];values=[]
        for column,value in [('evidence_family',family),('evidence_contract',c['target_contract']),('evidence_parser',c['target_parser']),('evidence_run',c['target_run']),('occurrence_id',c['target_occurrence_id']),('source_path',c['target_source_path']),('source_pointer',c['target_pointer'])]:
            if value is not None:conditions.append(column+'=?');values.append(value)
        exact_identity=bool(c['target_run'] and c['target_occurrence_id'])
        exact_coordinates=bool(family and c['target_source_path'] and c['target_pointer'])
        index_hint=''
        if exact_coordinates:
            if not hasattr(self,'_physical_source_scopes'):self._physical_source_scopes={}
            if family not in self._physical_source_scopes:
                self._physical_source_scopes[family]=[r[0] for r in self.db.execute('SELECT DISTINCT source_key FROM research_container WHERE evidence_family=?',(family,))]
            keys=self._physical_source_scopes[family]
            if not keys:return None
            conditions.append('source_key IN ('+','.join('?' for _ in keys)+')');values.extend(keys)
            # With several exact scopes SQLite can otherwise prefer the
            # family/contract prefix and scan all NPC records per hop.
            # This frozen index matches all three physical coordinates.
            index_hint=' INDEXED BY research_container_path'
        if conditions and (exact_identity or exact_coordinates):
            rows=self.db.execute('SELECT * FROM research_container'+index_hint+' WHERE '+' AND '.join(conditions)+' LIMIT 2',values).fetchall()
            if len(rows)==1:
                self._recheck_container(rows[0]);return self._container_ref(rows[0])
        return None

    def follow(self,*args,**kwargs):
        result=super().follow(*args,**kwargs)
        ref=kwargs.get('evidence_ref',args[1] if len(args)>1 else None)
        decoded=decode_ref(ref)
        if decoded['kind']=='text':
            text=self._text_for(decoded)
            container=self.db.execute('SELECT * FROM research_container WHERE id=?',(text['container_id'],)).fetchone()
        else:container=self._container_for(decoded)
        mode=self.components[container['source_key']]['adapter']
        if mode in {'event_physical_16','event_physical_18','native_successor_physical','native_high_value_physical','talk_physical'}:
            raw=dict(self._recheck_container(container))
            prefix='reuse_' if mode in {'native_successor_physical','native_high_value_physical'} else 'target_'
            occurrence=raw.get(prefix+'occurrence_id')
            if occurrence is not None:
                candidate={name:raw.get(prefix+name.removeprefix('target_')) for name in ('target_family','target_contract','target_parser','target_run','target_occurrence_id')}
                candidate.update(target_source_path=raw['source_path'],target_pointer=raw.get('pointer',raw.get('source_pointer',container['source_pointer'])))
                target=self._target_ref(candidate) if kwargs.get('resolve_targets',True) else None
                # A one-edge, source-authored reuse observation is separate
                # from the stored relation pages. It is never a logical join.
                result['physical_reuse_observations']=[{**candidate,'target_evidence_ref':target,'status':'physical_candidate','raw_sha256':raw.get('raw_sha256'),'source_file_sha256':container['source_file_sha256'],'relation_kind':'exact_projection_reuse','source_occurrence':container['occurrence_id']}]
                result['partial']=True;result['scope_complete']=False
            elif mode=='talk_physical' and raw['classification']=='mapping_unresolved_terminal':
                result['partial']=True;result['scope_complete']=False
                result['terminal_limitations']=['pinned physical file exists; its terminal structure has no proven dialogue navigation']
        result['relation_semantics']='stored physical candidates only; cardinality is not logical identity'
        result['target_coordinate_decoders']=['accepted narrative trio namespaces; original locators preserved','Document empty file-root locator -> Readable file-root view /']
        result['logical_identity_proven']=False
        result['product_accepted']=False
        unresolved_targets=sum(c.get('target_evidence_ref') is None for r in result.get('items',[]) for c in r.get('candidates',[])) if kwargs.get('resolve_targets',True) else None
        result['unresolved_target_count']=unresolved_targets
        if unresolved_targets:
            result['partial']=True;result['scope_complete']=False
        return result
