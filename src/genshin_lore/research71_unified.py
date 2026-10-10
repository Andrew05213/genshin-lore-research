"""Family-specific agent views of the qualified unified candidate.

No legacy bounded provider is instantiated. The existing index is discovery;
CandidateResearch71 rechecks every delivered container/text against its sealed
artifact. Reverse navigation reads the declared Lore/Voice/Quest candidate
tables, not same-ID guesses. Wrapper refs retain the underlying Research ref.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .research71_candidate_query import CandidateResearch71
from .research_refs import decode_ref
from .research_7_1_bounded import SNAPSHOT, token, untoken
from .research_source_context import ResearchSourceContext
from .research71_candidate_adapters import fetch

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "research71-unified:"


class UnifiedReadable:
    """The existing Book continuation's small, qualified line interface."""
    def __init__(self, runtime):
        self.runtime = runtime
        self.db = {"readable_archive": runtime.query._source("7.1-readable_archive")}

    def _readable(self, text_id):
        row = self.db["readable_archive"].execute(
            "SELECT l.*,f.source_path FROM readable_line l JOIN readable_file f ON f.id=l.file_id WHERE l.id=?",
            (text_id,)).fetchone()
        if row is None:
            raise KeyError(text_id)
        indexed = self.runtime.query.db.execute(
            "SELECT * FROM research_text WHERE source_key='7.1-readable_archive' AND source_table='readable_line' AND pk_value=?", (text_id,)).fetchone()
        if indexed is None:
            raise ValueError("Readable line absent from qualified index")
        self.runtime.query._recheck_text(indexed)
        return row, indexed

    def read(self, local):
        return self.runtime.read(self.runtime.ref(self.runtime.entries['readable_archive'], local))['data']


class Research71Unified:
    def __init__(self, *, corpus, bindings, index, profile, released=False):
        self.released = released
        if released:
            from .research71_release import ReleasedResearch71
            self.query = ReleasedResearch71(corpus, bindings, index, profile)
        else:
            self._candidate_query(corpus, bindings, index, profile)
        self._entries()

    def _candidate_query(self, corpus, bindings, index, profile):
        if json.loads(Path(profile).read_text()).get('successor_contract')=='research71-discovery-navigation-v1.1':
            from .research71_successor import SuccessorResearch71
            self.query=SuccessorResearch71(corpus,bindings,index,profile)
            return
        qualification=json.loads((ROOT/'manifests/research/candidate-7.1-agent-runtime-v2.json').read_text())
        if (qualification.get('contract')!='research71-unified-agent-runtime-v1' or
                qualification.get('status')!='local_candidate_qualification' or qualification.get('snapshot')!=SNAPSHOT or
                qualification.get('product_accepted') is not False or qualification.get('full_research_7_1_accepted') is not False or
                qualification.get('legacy_fallback') is not False):
            raise ValueError('unified agent runtime candidate authority mismatch')
        for path, expected in qualification['implementation_sha256'].items():
            if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=expected:
                raise ValueError('unified agent runtime implementation drift: '+path)
        for path,key in ((corpus,'corpus_manifest_sha256'),(profile,'candidate_profile_sha256')):
            if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=qualification[key]:
                raise ValueError('unified candidate configuration drift')
        self.query = CandidateResearch71(corpus, bindings, index, profile, allow_prepublication_profile=True)

    def _entries(self):
        self.corpus_ref = self.query.corpus_ref
        self.labels = ResearchSourceContext(self.query.db, self.query._source('7.1-document_archive'))
        self.entries = {}
        # Old registry supplies capability naming/limitations only, never paths,
        # seals, parser/run identities or provider constructors.
        names = json.loads((ROOT / 'manifests/research-7.1/7.1-capability-registry-v1.json').read_text())
        components = self.query.components
        by_cap = {v.get('registered_capability_id'): k for k,v in components.items() if v.get('registered_capability_id')}
        for old in names['capabilities']:
            cid = old['capability_id']
            key = '7.1-character-foundation' if cid.startswith('character_') else by_cap.get(cid)
            if key is None:
                raise ValueError('unified capability binding absent: ' + cid)
            c = components[key]
            self.entries[cid] = {**old, 'source_key': key, 'route': 'source_context' if cid == 'readable_archive' else 'unified',
                'family': c['family'], 'contract': c['contract'], 'parser': c.get('parser', c.get('parser_build_id')),
                'run': c.get('run', c.get('ingest_run_id')), 'artifacts': [self._artifact(key)],
                'backend': 'unified_release' if self.released else 'unified_candidate',
                'limitations': list(old['limitations']) + [
                    'bounded published 7.1; historical gaps and candidate semantics remain' if self.released
                    else 'unified local candidate; not current authority']}
        self.registry = {'capabilities': list(self.entries.values()),
                         'backend': 'unified_release' if self.released else 'unified_candidate', 'snapshot': SNAPSHOT}
        if self.released:
            self.registry.update(release_id=self.query.release_id, historical_contract_gaps=8,
                                 historical_component_gaps=12, full_research_7_1_accepted=False)

    def _artifact(self, key):
        c = self.query.components[key]
        return {'path': str(self.query.bindings['artifacts'][key]), **c['artifact']}

    def close(self):
        self.query.close()

    def entry(self, cid, operation):
        e = self.entries[cid]
        if operation not in e['supported_operations']:
            raise ValueError('unsupported capability operation')
        return e

    def adapter(self, entry):
        if entry['capability_id'] != 'readable_archive':
            raise ValueError('only qualified Readable line adapter is exposed')
        return UnifiedReadable(self)

    def _wrap(self, cid, base, child=None):
        return PREFIX + token({'snapshot': SNAPSHOT, 'corpus': self.corpus_ref, 'capability_id': cid,
                              'base_ref': base, 'codex_child': child})

    def ref(self, entry, local):
        if 'base_ref' in local and 'text_id' not in local:
            return self._wrap(entry['capability_id'], local['base_ref'], local.get('codex_child'))
        if entry['capability_id'] != 'readable_archive':
            raise ValueError('no guessed occurrence refs')
        row = self.query.db.execute("SELECT * FROM research_text WHERE source_key='7.1-readable_archive' AND source_table='readable_line' AND pk_value=?", (local['text_id'],)).fetchone()
        parent = None if row is None else self.query.db.execute('SELECT * FROM research_container WHERE id=?', (row['container_id'],)).fetchone()
        if row is None or (parent['source_path'], row['text_source_pointer'], row['evidence_run']) != (local['source_path'], local['pointer'], local['run_id']):
            raise ValueError('Readable continuation binding mismatch')
        self.query._recheck_text(row)
        return self._wrap(entry['capability_id'], self.query._text_ref(row))

    def _rows(self, base):
        ref = decode_ref(base)
        if ref['kind'] == 'text':
            text = self.query._text_for(ref)
            parent = self.query.db.execute('SELECT * FROM research_container WHERE id=?', (text['container_id'],)).fetchone()
            self.query._recheck_container(parent)
            return parent, text
        return self.query._container_for(ref), None

    def _cap(self, parent):
        key = parent['source_key']
        if key == '7.1-character-foundation':
            source = self.query._source(key)
            row = source.execute('SELECT * FROM foundation_primary_row WHERE id=?', (parent['occurrence_id'],)).fetchone()
            path = row['source_path']
            if path.endswith('/AvatarExcelConfigData.json'): return 'character_avatar'
            if path.endswith('/FetterStoryExcelConfigData.json'): return 'character_story'
            if path.endswith('/FettersExcelConfigData.json'): return 'character_voice'
            return 'character_fetter_info'
        return self.query.components[key].get('registered_capability_id')

    def unref(self, evidence_ref, operation):
        if not isinstance(evidence_ref, str) or not evidence_ref.startswith(PREFIX):
            raise ValueError('foreign unified evidence ref; legacy fallback forbidden')
        local = untoken(evidence_ref[len(PREFIX):])
        if local.get('snapshot') != SNAPSHOT or local.get('corpus') != self.corpus_ref:
            raise ValueError('unified snapshot/corpus mismatch')
        parent, text = self._rows(local['base_ref'])
        cid = self._cap(parent)
        if cid != local['capability_id']:
            raise ValueError('capability/evidence binding mismatch')
        if local.get('codex_child'):
            if cid != 'codexquest_archive': raise ValueError('foreign structural child')
            child = self.query._source(parent['source_key']).execute('SELECT * FROM codex_child WHERE id=? AND root_id=?', (local['codex_child'], parent['occurrence_id'])).fetchone()
            if child is None or hashlib.sha256(child['raw_json'].encode()).hexdigest() != child['raw_sha256']:
                raise ValueError('Codex child identity/seal mismatch')
        if cid == 'readable_archive':
            if text is None: raise ValueError('Readable requires exact physical line')
            local.update(text_id=text['child_id'], source_path=parent['source_path'], pointer=text['text_source_pointer'], run_id=text['evidence_run'])
        return self.entry(cid, operation), local

    def _text_rows(self, parent, text=None, child=None):
        rows = [text] if text is not None else self.query.db.execute(
            "SELECT * FROM research_text INDEXED BY research_text_container WHERE container_id=? AND locale='CHS' ORDER BY pointer,owner_field,id", (parent['id'],)).fetchall()
        if child:
            pointer = self.query._source(parent['source_key']).execute('SELECT source_pointer FROM codex_child WHERE id=?', (child,)).fetchone()[0]
            db=self.query._source(parent['source_key'])
            scoped=[]
            for r in rows:
                candidate=fetch(db,r['source_table'],r['pk_column'],r['pk_value'])
                owner=db.execute('SELECT source_pointer FROM codex_text_slot WHERE id=?',(candidate['slot_id'],)).fetchone()[0]
                if owner==pointer or owner.startswith(pointer+'/'):scoped.append(r)
            rows=scoped
        return rows

    def _context(self, parent, rows):
        if parent['evidence_family']=='readable_archive':
            context=self.labels.for_evidence(parent, rows[0] if rows else None)
            for title in context.get('title_candidates', []):
                title_row=self.query.db.execute("SELECT * FROM research_text WHERE source_key='7.1-document_archive' AND source_table='document_text' AND pk_value=?", (title['title_text_id'],)).fetchone()
                if title_row is None:raise ValueError('qualified source title missing')
                self.query._recheck_text(title_row)
            return context
        titles = [r for r in rows if r['owner_field'] in ('nameTextMapHash','titleTextMapHash','storyTitleTextMapHash','voiceTitleTextMapHash') and r['locale']=='CHS']
        # Label uniqueness is a display observation, never logical identity.
        title = titles[0]['raw_text'] if len(titles) == 1 else None
        return {'display_title': title, 'title_resolution_status': 'resolved' if title else 'unresolved',
                'title_basis': 'source-authored field; same physical owner' if title else None,
                'candidate_titles': [r['raw_text'] for r in titles], 'source_kind': parent['evidence_family']}

    def read(self, evidence_ref):
        entry, local = self.unref(evidence_ref, 'read')
        parent, text = self._rows(local['base_ref'])
        rows = self._text_rows(parent, text, local.get('codex_child'))
        # Existing Research read/inspect are the qualified audit path as well.
        self.query.read(self.corpus_ref, local['base_ref'])
        candidates = []
        for row in rows:
            self.query._recheck_text(row)
            proof = self.query._proof(row)
            role = row['owner_field']
            if entry['capability_id']=='quest_talk_physical': role='talk_node_text'
            candidates.append({**proof, 'id': row['child_id'], 'owner_field': role, 'raw_text': row['raw_text'],
                'source_path': row['text_source_path'], 'pointer': row['text_source_pointer'],
                'pair_ordinal': row['physical_pair_ordinal'], 'formal_evidence_ref': self.query._text_ref(row),
                'evidence_identity': self.query._text_provenance(row)})
        data = {'text_candidates': candidates, 'single_physical_container': True,
                'formal_container_ref': self.query._container_ref(parent), 'formal_text_refs': [c['formal_evidence_ref'] for c in candidates]}
        pointer = parent['source_pointer']
        if entry['capability_id']=='readable_archive':
            pointer=text['text_source_pointer']
            data={'raw_text':text['raw_text'], 'locale':text['locale'], 'text_provenance':{
                'source_path':text['text_source_path'],'pointer':pointer,'pair_ordinal':text['physical_pair_ordinal']}, **data}
            data['direct_text_identity']={'formal_evidence_ref':self.query._text_ref(text),'evidence_identity':self.query._text_provenance(text)}
            data.pop('text_candidates')  # Direct source line, not duplicated TextMap ownership.
        if entry['capability_id']=='codexquest_archive':
            db=self.query._source(parent['source_key'])
            root=dict(db.execute('SELECT * FROM codex_root WHERE id=?',(parent['occurrence_id'],)).fetchone())
            data['structure']={k:root[k] for k in ('shape','section_state','section_count','semantic_mapping')}
            if local.get('codex_child'):
                child=dict(db.execute('SELECT * FROM codex_child WHERE id=?',(local['codex_child'],)).fetchone())
                pointer=child['source_pointer'];data['structure'].update({k:child[k] for k in ('id','kind','source_pointer','physical_ordinal','raw_sha256')})
            data['structure']['semantic_order_proven']=False
        context=self._context(parent, rows)
        label=context.get('citation_label') or context['display_title'] or f"{parent['source_path']} {pointer}（物理来源；标题未解析）"
        artifacts={parent['source_key'], *(r['source_key'] for r in rows)}
        if entry['capability_id']=='readable_archive':artifacts.add('7.1-document_archive')
        return {'evidence_ref':evidence_ref,'snapshot':SNAPSHOT,'capability_id':entry['capability_id'],
            'family':parent['evidence_family'],'contract':parent['evidence_contract'],'parser':parent['evidence_parser'],
            'run':parent['evidence_run'],'artifacts':[self._artifact(k) for k in sorted(artifacts)],
            'capability_status':entry['status'],'limitations':entry['limitations'], 'source_context':context,
            'citation_label':label,'source_path':parent['source_path'],'pointer':pointer,'data':data,
            'backend':'unified_release' if self.released else 'unified_candidate',
            'corpus_ref':self.corpus_ref,'product_accepted':self.released,'partial':True}

    def inspect(self, evidence_ref):
        entry, local=self.unref(evidence_ref,'inspect')
        self.query.inspect(self.corpus_ref,local['base_ref'])
        return self.read(evidence_ref)

    def search(self, query, capability_id, *, limit=10, cursor=None):
        entry=self.entry(capability_id,'search')
        if not isinstance(query,str) or not query or type(limit) is not int or not 1<=limit<=50:
            raise ValueError('bounded literal search required')
        scope={'op':'search','query':query,'capability':capability_id,'limit':limit,'corpus':self.corpus_ref}
        after=self._cursor(cursor,scope)
        key='7.1-character-text' if capability_id.startswith('character_') else entry['source_key']
        # Same immutable index, literal substring fallback and per-hit source reread.
        if capability_id=='readable_archive':
            rows=self.query.db.execute("SELECT t.* FROM research_text t INDEXED BY sqlite_autoindex_research_text_1 WHERE t.source_key=? AND t.locale='CHS' AND instr(t.raw_text,?)>0 AND t.id>? ORDER BY t.id",(key,query,after))
        else:
            # One stable first matching text per exact physical container over
            # the WHOLE query, not just the current page. Advancing the cursor
            # must not let a later matching text reintroduce the same parent.
            rows=self.query.db.execute("SELECT t.* FROM research_text t WHERE t.id IN (SELECT MIN(m.id) FROM research_text m INDEXED BY sqlite_autoindex_research_text_1 WHERE m.source_key=? AND m.locale='CHS' AND instr(m.raw_text,?)>0 GROUP BY m.container_id HAVING MIN(m.id)>?) ORDER BY t.id",(key,query,after))
        if capability_id=='readable_archive':
            rows=list(rows)
            # Source-authored Document title -> sealed physical Readable file.
            # Reuse the accepted title adapter, never filename guessing.
            doc=self.query._source('7.1-document_archive')
            paths=doc.execute("SELECT DISTINCT c.target_source_path FROM document_text t JOIN document_slot s ON s.id=t.slot_id JOIN document_relation r ON r.row_id=s.row_id JOIN document_candidate c ON c.relation_id=r.id WHERE s.field='titleTextMapHash' AND s.locale='CHS' AND instr(t.raw_text,?)>0 AND r.kind='localization_readable'",(query,)).fetchall()
            for (path,) in paths:
                parents=self.query.db.execute("SELECT * FROM research_container WHERE source_key=? AND source_path=?",(key,path)).fetchall()
                for parent in parents:
                    self.query._recheck_container(parent)
                    titles=self.labels._readable_document_titles(path,parent['source_file_sha256'])
                    matching=[t for t in titles if query in t['title']]
                    if not matching:continue
                    for title in matching:
                        row=self.query.db.execute("SELECT * FROM research_text WHERE source_key='7.1-document_archive' AND source_table='document_text' AND pk_value=?",(title['title_text_id'],)).fetchone()
                        if row is None:raise ValueError('source title missing from unified index')
                        self.query._recheck_text(row)
                    first=self.query.db.execute("SELECT * FROM research_text WHERE container_id=? AND locale='CHS' ORDER BY ordinal,child_id,id LIMIT 1",(parent['id'],)).fetchone()
                    if first is not None and first['id']>after:rows.append(first)
            rows=sorted(rows,key=lambda r:r['id'])
        selected=[];seen=set()
        for row in rows:
            parent=self.query.db.execute('SELECT * FROM research_container WHERE id=?',(row['container_id'],)).fetchone()
            if self._cap(parent)!=capability_id: continue
            identity=row['id'] if capability_id=='readable_archive' else parent['id']
            if identity in seen: continue
            seen.add(identity);self.query._recheck_text(row)
            base=self.query._text_ref(row) if capability_id=='readable_archive' else self.query._container_ref(parent)
            selected.append((row['id'],self._wrap(capability_id,base)))
            if len(selected)>limit: break
        refs=[ref for _,ref in selected[:limit]]
        return {'results':[{'evidence_ref':r,'data':{}} for r in refs],
                'next_cursor':token({'scope':scope,'after':selected[limit-1][0]}) if len(selected)>limit else None,
                'partial':True,'backend':'unified_release' if self.released else 'unified_candidate'}

    @staticmethod
    def _cursor(cursor, scope):
        if cursor is None:return 0
        value=untoken(cursor)
        if value.get('scope')!=scope: raise ValueError('unified cursor scope mismatch')
        return value['after']

    def _target(self,key,occurrence):
        c=self.query.components[key]
        rows=self.query.db.execute('SELECT * FROM research_container WHERE source_key=? AND evidence_family=? AND evidence_contract=? AND evidence_parser=? AND evidence_run=? AND occurrence_id=?',(key,c['family'],c['contract'],c['parser'],c['run'],occurrence)).fetchall()
        if len(rows)!=1: return None
        parent=rows[0];self.query._recheck_container(parent)
        cid=self._cap(parent)
        return self._wrap(cid,self.query._container_ref(parent)) if cid else None

    def follow(self, evidence_ref, *, limit=20, cursor=None):
        entry,local=self.unref(evidence_ref,'follow')
        if type(limit) is not int or not 1<=limit<=50:raise ValueError('follow limit 1..50')
        scope={'op':'follow','ref':evidence_ref,'limit':limit,'corpus':self.corpus_ref}
        parent,_=self._rows(local['base_ref']);cid=entry['capability_id'];edges=[]
        after=self._cursor(cursor,scope) if cid in {'character_avatar','quest_narrative_archive','quest_talk_physical','codexquest_archive','narrative_npc'} else 0
        if cid=='character_avatar':
            for key,table,target in [('7.1-character-lore','lore_avatar_candidate','character_story'),('7.1-character-voice','voice_avatar_candidate','character_voice')]:
                db=self.query._source(key)
                for row in db.execute(f'SELECT * FROM {table} WHERE avatar_row_id=? ORDER BY row_id,relation_id',(parent['occurrence_id'],)):
                    ref=self._target('7.1-character-foundation',row['row_id'])
                    if ref and self.unref(ref,'read')[0]['capability_id']==target:
                        edges.append({'target_ref':ref,'status':'physical_candidate','source_key':key,'raw_candidate':dict(row)})
        elif cid=='quest_narrative_archive':
            db=self.query._source(parent['source_key'])
            for row in db.execute('SELECT * FROM excel_candidate WHERE source_row_id=? ORDER BY id',(parent['occurrence_id'],)):
                edges.append({'target_ref':self._target(parent['source_key'],row['target_main_row_id']),'status':'physical_candidate','raw_candidate':dict(row)})
            for row in db.execute('SELECT * FROM main_root_candidate WHERE main_row_id=? ORDER BY id',(parent['occurrence_id'],)):
                edges.append({'target_ref':self._target('7.1-quest_talk_physical',row['quest_talk_container_id']),'status':row['status'],'raw_candidate':dict(row)})
        elif cid=='quest_talk_physical':
            db=self.query._source(parent['source_key'])
            for row in db.execute('SELECT * FROM physical_reference WHERE container_id=? ORDER BY id',(parent['occurrence_id'],)):
                for candidate in json.loads(row['candidates_json']):
                    target=db.execute('SELECT * FROM physical_container WHERE id=?',(candidate['container_id'],)).fetchone()
                    if target is None or (target['source_path'],target['pointer'],target['kind'],target['ingest_run_id'])!=(candidate['path'],candidate['pointer'],candidate['kind'],entry['run']):
                        raise ValueError('Quest/Talk exact target source binding drift')
                    ref=self._target(parent['source_key'],candidate['container_id'])
                    edges.append({'target_ref':ref,'status':row['status'],'raw_relation':dict(row),'candidate':candidate})
        elif cid=='narrative_npc':
            # Invert an explicit frozen Dialog -> NPC physical candidate, not
            # a lexical name or native-ID equality relation.
            db=self.query._source('7.1-narrative_dialog')
            count=db.execute('SELECT count(*) FROM physical_candidate WHERE target_source_path=? AND target_pointer=?',(parent['source_path'],parent['source_pointer'])).fetchone()[0]
            rows=db.execute('SELECT * FROM physical_candidate WHERE target_source_path=? AND target_pointer=? ORDER BY row_ordinal,field,item_ordinal,target_ordinal LIMIT ? OFFSET ?',(parent['source_path'],parent['source_pointer'],limit,after)).fetchall()
            for row in rows:
                locator=db.execute('SELECT * FROM locator WHERE row_ordinal=? AND field=? AND item_ordinal=?',(row['row_ordinal'],row['field'],row['item_ordinal'])).fetchone()
                physical=db.execute('SELECT * FROM physical_row WHERE ordinal=?',(row['row_ordinal'],)).fetchone()
                target=self.query.db.execute("SELECT * FROM research_container WHERE source_key='7.1-narrative_dialog' AND source_path=? AND source_pointer=?",(physical['source_path'],physical['pointer'])).fetchall()
                if len(target)!=1:raise ValueError('Dialog source occurrence missing/ambiguous')
                self.query._recheck_container(target[0])
                edges.append({'target_ref':self._wrap('narrative_dialog',self.query._container_ref(target[0])),'status':'physical_candidate','raw_candidate':dict(row),'raw_locator':dict(locator)})
            return {'edges':edges,'next_cursor':token({'scope':scope,'after':after+limit}) if after+limit<count else None,
                    'partial':True,'candidate_count':count,'locator_states':[{'state':'unresolved','reason':'inverse physical candidates; logical speaker not proved'}]}
        elif cid=='codexquest_archive':
            db=self.query._source(parent['source_key']);prefix='/'
            if local.get('codex_child'):
                prefix=db.execute('SELECT source_pointer FROM codex_child WHERE id=?',(local['codex_child'],)).fetchone()[0]+'/'
            children=db.execute('SELECT * FROM codex_child WHERE root_id=? ORDER BY source_pointer,id',(parent['occurrence_id'],)).fetchall()
            for child in children:
                p=child['source_pointer']
                if (not local.get('codex_child') and child['kind']=='section_like') or (local.get('codex_child') and child['kind']=='node_like' and p.startswith(prefix)):
                    edges.append({'target_ref':self._wrap(cid,local['base_ref'],child['id']),'status':'physical_candidate','source_pointer':p,'node_role_status':'versioned_unresolved'})
        else:
            page=self.query.follow(self.corpus_ref,local['base_ref'],limit=limit,cursor=cursor)
            for relation in page['items']:
                for candidate in relation.get('candidates',[]):
                    base=candidate.get('target_evidence_ref')
                    ref=None
                    if base:
                        target,_=self._rows(base);target_cid=self._cap(target)
                        if target_cid=='readable_archive' and decode_ref(base)['kind']=='container':
                            first=self.query.db.execute("SELECT * FROM research_text WHERE container_id=? AND locale='CHS' ORDER BY ordinal,child_id,id LIMIT 1",(target['id'],)).fetchone()
                            if first is not None:ref=self._wrap(target_cid,self.query._text_ref(first))
                        elif target_cid:ref=self._wrap(target_cid,base)
                    edges.append({'target_ref':ref,'status':candidate.get('status',relation['status']),'relation':relation,'candidate':candidate})
            for reuse in page.get('physical_reuse_observations',[]):
                base=reuse.get('target_evidence_ref');ref=None
                if base:
                    target,_=self._rows(base);target_cid=self._cap(target)
                    if target_cid:ref=self._wrap(target_cid,base)
                edges.append({'target_ref':ref,'status':'physical_candidate','reuse_observation':reuse})
            states=list(page.get('terminal_states',[]))
            states.extend({'state':'terminal_unresolved','reason':reason} for reason in page.get('terminal_limitations',[]))
            if page['partial']:states=states+[{'state':'unresolved','reason':'declared relation scope is partial; empty is not complete'}]
            return {'edges':edges,'next_cursor':page['next_cursor'],'partial':page['partial'],'locator_states':states}
        page=edges[after:after+limit]
        return {'edges':page,'next_cursor':token({'scope':scope,'after':after+limit}) if after+limit<len(edges) else None,
                'partial':True,'candidate_count':len(edges),'locator_states':[{'state':'unresolved','reason':'physical candidates; no logical ownership/order'}]}
