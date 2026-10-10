"""Opt-in v1.1 candidate on v1's unchanged, qualified evidence identities.

Only discovery and source-backed family navigation differ. No legacy fallback.
"""
import json
import os
from pathlib import Path

from .research71_candidate_query import CandidateResearch71
from .research_refs import decode_ref
from .research_index import sha256_file
from .research_query import ResearchUnavailable
from .semantic_discovery import SemanticDiscovery

ROOT=Path(__file__).resolve().parents[2]
PROFILE='manifests/research/research71-v1-1-candidate-profile.json'
RESCUE_PROFILE='manifests/research/research71-v1-1-rescue-profile.json'


class WindowSemanticDiscovery(SemanticDiscovery):
    def __init__(self,config,*args):
        from importlib.metadata import version
        from .semantic_discovery import SemanticUnavailable
        expected=config.get('embedding_environment',{})
        if set(expected)!={'fastembed','onnxruntime','numpy','hnswlib'} or any(version(k)!=v for k,v in expected.items()):
            raise SemanticUnavailable('7.1 embedding implementation version mismatch')
        super().__init__(config,*args)

    def candidates_from_vector(self,vector,k=None):
        hits=super().candidates_from_vector(vector,k)
        # Multiple windows are views of ONE occurrence, never separate
        # citation identities. Different occurrences with equal text stay.
        seen=set();result=[]
        for hit in hits:
            if hit['research_text_id'] in seen:continue
            seen.add(hit['research_text_id']);result.append(hit)
        return result


class SuccessorResearch71(CandidateResearch71):
    def _semantic_discovery(self,config,index_sha256):
        return WindowSemanticDiscovery(config,self.bindings,self.corpus_ref,index_sha256)

    def __init__(self, corpus, bindings, index, profile):
        if Path(profile).resolve() not in {(ROOT/PROFILE).resolve(), (ROOT/RESCUE_PROFILE).resolve()}:
            raise ResearchUnavailable('exact v1.1 candidate profile required')
        config=json.loads(Path(profile).read_text())
        if config.get('successor_contract')!='research71-discovery-navigation-v1.1' or config.get('publishable') is not False:
            raise ResearchUnavailable('unqualified successor authority')
        for path,expected in config['successor_implementation_sha256'].items():
            if sha256_file(ROOT/path)!=expected:
                raise ResearchUnavailable('successor implementation drift: '+path)
        super().__init__(corpus,bindings,index,profile,allow_prepublication_profile=True)
        self.production_retrieval=config['sparse_first_policy']
        self.domain_map=config['discovery_domains']
        # This view reuses exact family candidates and their source checks. It
        # does not start a second provider or read old-preview artifact paths.
        from .research71_unified import Research71Unified
        self.navigation=Research71Unified.__new__(Research71Unified)
        self.navigation.released=False
        self.navigation.query=self
        self.navigation._entries()
        self.relevance_cleanup = config.get('relevance_cleanup') == 'finite-source-hints-v1'
        if config.get('citation_handoff') == 'research-citation-handoff-v1':
            from .citation_handoff import CitationHandoff
            self.citation_handoff = CitationHandoff(os.environ.get('GENSHIN_CITATION_HANDOFF_DIR'))

    def _rank_discovery_candidates(self, query, ranked, mode, domain=None):
        if not self.relevance_cleanup or not ranked:
            return ranked
        # Exact source-authored names are hints, not resolved entities. Several
        # equal-name occurrences and all nonmatching candidates are retained.
        anchors = self.db.execute("SELECT t.*,c.occurrence_id,c.source_path AS parent_path "
            "FROM research_text t JOIN research_container c ON c.id=t.container_id "
            "WHERE t.source_key='7.1-character-text' AND t.locale='CHS' "
            "AND t.owner_field='nameTextMapHash' AND c.source_path='ExcelBinOutput/AvatarExcelConfigData.json' "
            "AND length(t.raw_text)>=2 AND instr(?,t.raw_text)>0", (query,)).fetchall()
        preferred = set()
        for anchor in anchors:
            self._recheck_text(anchor)
            preferred.add(anchor['container_id'])
            for key, table in [('7.1-character-lore','lore_avatar_candidate'),
                               ('7.1-character-voice','voice_avatar_candidate')]:
                source = self._source(key)
                for candidate in source.execute(f'SELECT row_id FROM {table} WHERE avatar_row_id=?', (anchor['occurrence_id'],)):
                    targets = self.db.execute("SELECT id FROM research_container WHERE source_key='7.1-character-foundation' AND occurrence_id=?", (candidate['row_id'],))
                    preferred.update(r['id'] for r in targets)
        narrative = any(x in query for x in ('剧情','故事','讲了什么','发生','梗概','交易','月光','离乡'))
        operational = any(x in query for x in ('怎么拍','机位','操作','按钮','玩法','摄影规则'))
        result = []
        for item in ranked:
            row = self.db.execute('SELECT * FROM research_text WHERE id=?', (item['research_text_id'],)).fetchone()
            hints = []; boost = 0.0
            if item.get('sparse_rank') is not None:
                boost += .006; hints.append('existing sparse match +0.006')
            if query in row['raw_text']:
                boost += .016; hints.append('full literal query +0.016')
            if row['container_id'] in preferred:
                boost += .015; hints.append('explicit source-name / existing physical owner candidate +0.015; NOT identity proof')
            container = self.db.execute('SELECT * FROM research_container WHERE id=?',(row['container_id'],)).fetchone()
            instruction = container['source_path'].endswith('/ActivityFilmfestFrameExcelConfigData.json')
            if instruction and narrative and not operational:
                boost -= .015; hints.append('source record type: camera instruction, narrative-query penalty -0.015')
            # Reciprocal rank normalizes existing cosine/lexical/RRF scales;
            # finite hints never remove candidates or modify source proof.
            score = 1/(60+item['final_rank']) + boost
            result.append({**item, 'relevance_hints': hints, 'relevance_hint_score': score,
                           'ranking_basis': item['ranking_basis']+'; finite-source-hints-v1 (not confidence)'})
        result.sort(key=lambda x: (-x['relevance_hint_score'], x['final_rank'], x['research_text_id']))
        for rank, item in enumerate(result,1):item['final_rank']=rank
        return result

    def describe(self,*args,**kwargs):
        result=super().describe(*args,**kwargs)
        result.update(successor_status='experimental_candidate',baseline_release='research71-bounded-v1',
                      formal_release=False,historical_contract_gaps=8,historical_component_gaps=12,
                      semantic_discovery_7_1_supported=True,discovery_domains=self.domain_map,
                      extra_navigation=['character_story_voice_candidate','quest_talk_physical_candidate','quest_child_physical_candidate','npc_dialogue_reverse_candidate'],
                      legacy_fallback=False)
        return result

    def _decorate_context(self,item,row,container=None):
        result=super()._decorate_context(item,row,container)
        if row is not None and hasattr(self,'navigation'):
            if container is None:
                container=self.db.execute('SELECT * FROM research_container WHERE id=?',(row['container_id'],)).fetchone()
            rows=self.navigation._text_rows(container)
            title_fields={'nameTextMapHash','titleTextMapHash','storyTitleTextMapHash','voiceTitleTextMapHash'}
            if container['source_key'] in ('7.1-event_narrative_12','7.1-event_narrative_15'):
                title_fields.add('markTitleTextMapHash')
            titles=[r for r in rows if r['owner_field'] in title_fields and r['locale']=='CHS']
            for title in titles:self._recheck_text(title)
            context=self.navigation._context(container,rows)
            if len(titles)==1 and not context.get('display_title'):
                context.update(display_title=titles[0]['raw_text'],title_resolution_status='physical_source_label',
                               title_basis='reread same physical row source-authored field; not logical identity')
            label=context.get('citation_label') or context.get('display_title')
            if label:
                result.update(source_context=context,citation_label=label,
                              display_label_is_identity=False)
            cid=self.navigation._cap(container)
            if cid in ('character_story','readable_archive'):
                result.update(bounded_view_ref=self.navigation._wrap(cid,self._text_ref(row)),
                              bounded_observation_index=0,
                              bounded_read_action='inspect_research71_preview with supplied ref and text_start/text_chars; same full-text identity')
        return result

    def _resolve_domain(self,query,domain):
        if domain is None:
            # Broad narrative RRF is an explicit candidate mode, not automatic
            # entity classification. Sparse is never dropped by absent intent.
            return 'narrative','explicit_hybrid_narrative_scope',self.domain_map['domains']['narrative']['families']
        return super()._resolve_domain(query,domain)

    def _discovery_search(self,query,locale,families,source_prefix,limit,cursor,mode,domain=None):
        if mode=='hybrid' and locale not in (None,'CHS'):
            # The shared production ANN route is CHS-only. Preserve the exact
            # requested locale instead of admitting CHS dense candidates.
            scope={'op':'successor_hybrid_non_chs','q':query,'locale':locale,'families':sorted(set(families or [])),
                   'prefix':source_prefix,'limit':limit,'domain':domain}
            state=self._after(cursor,scope)
            if state==0:inner=None
            elif isinstance(state,dict) and set(state)=={'inner'}:inner=state['inner']
            else:raise ValueError('non-CHS hybrid cursor mismatch')
            value=self.search(self.corpus_ref,query,locale,families,source_prefix,limit,inner,'literal')
            value['next_cursor']=self._cursor(scope,{'inner':value['next_cursor']}) if value['next_cursor'] else None
            value.update(partial=True,semantic_unsupported_scopes=['non-CHS dense discovery'],
                         candidate_retrieval_complete=False)
            return value
        return super()._discovery_search(query,locale,families,source_prefix,limit,cursor,mode,domain)

    def follow(self,corpus_ref,evidence_ref,relation_kind=None,limit=20,cursor=None,resolve_targets=True):
        self._require(corpus_ref)
        decoded=decode_ref(evidence_ref)
        if decoded['kind']=='text':
            text=self._text_for(decoded)
            parent=self.db.execute('SELECT * FROM research_container WHERE id=?',(text['container_id'],)).fetchone()
        else:parent=self._container_for(decoded)
        self._recheck_container(parent)
        if relation_kind=='quest_child_physical_candidate':
            return self._quest_children(parent,evidence_ref,limit,cursor,resolve_targets)
        cid=self.navigation._cap(parent)
        kinds={'character_avatar':'character_story_voice_candidate','quest_narrative_archive':'quest_talk_physical_candidate',
               'quest_talk_physical':'quest_talk_physical_candidate','narrative_npc':'npc_dialogue_reverse_candidate'}
        if cid not in kinds or relation_kind not in (None,kinds[cid]):
            return super().follow(corpus_ref,evidence_ref,relation_kind,limit,cursor,resolve_targets)
        if type(limit) is not int or not 1<=limit<=50:
            raise ValueError('source-backed navigation limit 1..50')
        scope={'op':'successor_follow','corpus':corpus_ref,'ref':evidence_ref,'kind':relation_kind,'limit':limit,'resolve_targets':resolve_targets}
        state=self._after(cursor,scope)
        if state==0:inner=None
        elif isinstance(state,dict) and set(state)=={'inner'}:inner=state['inner']
        elif isinstance(state,dict) and set(state)=={'stored'} and relation_kind is None:
            # Appended discovery must not hide v1's stored/dynamic relations.
            value=super().follow(corpus_ref,evidence_ref,None,limit,state['stored'],resolve_targets)
            value['next_cursor']=self._cursor(scope,{'stored':value['next_cursor']}) if value['next_cursor'] else None
            return value
        else:raise ValueError('source-backed navigation cursor state mismatch')
        wrapped=self.navigation._wrap(cid,evidence_ref)
        page=self.navigation.follow(wrapped,limit=limit,cursor=inner)
        items=[]
        for edge in page['edges']:
            target=edge.get('target_ref')
            base=self.navigation.unref(target,'read')[1]['base_ref'] if target else None
            # Keep every source edge, even if distinct edges share a target.
            items.append({'relation_kind':kinds[cid],'status':'physical_candidate','search_complete':False,
                          'source_evidence_ref':evidence_ref,'source_proof':{k:v for k,v in edge.items() if k!='target_ref'},
                          'candidates':[{'target_evidence_ref':base if resolve_targets else None,
                                         'status':'physical_candidate','logical_identity_proven':False}]})
        next_cursor=self._cursor(scope,{'inner':page['next_cursor']}) if page['next_cursor'] else None
        if next_cursor is None and relation_kind is None:
            next_cursor=self._cursor(scope,{'stored':None})
        return {'items':items,'next_cursor':next_cursor,
                'candidate_count':page.get('candidate_count'),'partial':True,'scope_complete':False,
                'relation_semantics':'source-authored physical navigation; not speaker, logical ownership, or narrative time',
                'logical_identity_proven':False,'unsupported_scopes':['historical eight-contract equivalence remains unproved'],
                'terminal_states':page.get('locator_states',[])}

    def _quest_children(self,parent,ref,limit,cursor,resolve_targets):
        key='7.1-quest_narrative_archive'
        if parent['source_key']!=key or parent['source_path']!='ExcelBinOutput/MainQuestExcelConfigData.json':
            raise ValueError('Quest child reverse locator requires an exact MainQuest occurrence')
        if type(limit) is not int or not 1<=limit<=50:raise ValueError('child limit 1..50')
        scope={'op':'quest_children','ref':ref,'limit':limit,'resolve_targets':resolve_targets,'corpus':self.corpus_ref}
        state=self._after(cursor,scope)
        if state==0:after=''
        elif isinstance(state,dict) and set(state)=={'after'} and isinstance(state['after'],str):after=state['after']
        else:raise ValueError('child cursor mismatch')
        src=self._source(key)
        rows=src.execute('SELECT * FROM excel_candidate WHERE target_main_row_id=? AND id>? ORDER BY id LIMIT ?',
                         (parent['occurrence_id'],after,limit+1)).fetchall()
        items=[]
        for row in rows[:limit]:
            # The candidate contains an explicit mainId/mainQuestID reference
            # and a bounded namespace resolution, not equal-ID entity matching.
            physical=src.execute('SELECT * FROM excel_row WHERE id=?',(row['source_row_id'],)).fetchone()
            target=src.execute('SELECT * FROM excel_row WHERE id=?',(row['target_main_row_id'],)).fetchone()
            field={'step_mainquest':'mainId','format_mainquest':'mainQuestID'}.get(row['role'])
            raw=json.loads(physical['raw_json'])
            if field is None or str(raw.get(field))!=target['native_id'] or not row['source_pointer'].endswith('/'+field):
                raise ResearchUnavailable('source-authored Quest parent reference drift')
            wrapped=self.navigation._target(key,row['source_row_id'])
            base=self.navigation.unref(wrapped,'read')[1]['base_ref'] if wrapped else None
            items.append({'relation_kind':'quest_child_physical_candidate','status':'physical_candidate',
                          'source_evidence_ref':ref,'source_proof':dict(row),'role':row['role'],
                          'candidates':[{'target_evidence_ref':base if resolve_targets else None,'logical_identity_proven':False}]})
        return {'items':items,'next_cursor':self._cursor(scope,{'after':rows[limit-1]['id']}) if len(rows)>limit else None,
                'partial':True,'scope_complete':False,'logical_identity_proven':False,
                'relation_semantics':'reverse explicit MainQuest source locator; child candidates, not narrative time or historical subId equivalence'}
