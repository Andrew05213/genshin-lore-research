"""Prepublication Quest Review 7.1 family-specific routed capability adapter.

The physical Review and exact-target overlay are separate artifacts and runs.
This provider does not register with the current 7.0 MCP server.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .family_evidence import FamilyEvidenceResult
from .quest_derived_7_1_bounded import QuestDerived71


class QuestReview71Provider:
    family = 'quest_review_archive'
    family_contract_version = 'quest-review-archive-7.1-bound-evidence-v2'
    coverage = 'complete_declared_7_1_quest_review_physical_text_and_exact_target_binding'

    def __init__(self, runtime: QuestDerived71):
        authority=json.loads((runtime.manifest_path.parent/'quest-review-binding-v2.json').read_text())
        adapter=authority['query_adapter']
        code=Path(__file__)
        if (adapter['contract']!=self.family_contract_version or adapter['path']!='src/genshin_lore/quest_review_7_1_provider.py'
                or hashlib.sha256(code.read_bytes()).hexdigest()!=adapter['sha256']):
            raise ValueError('Quest Review 7.1 provider implementation seal')
        self.runtime = runtime
        self.db = runtime.db[self.family]
        self.binding = runtime.binding
        self.run = runtime.runs[self.family]

    def capability(self) -> dict:
        return {'family':self.family,'family_contract_version':self.family_contract_version,
                'supported_operations':['list_family_evidence','get_family_evidence','search_family_text'],
                'supported_filters':{'list':['record_kind','row_kind','native_id','container_id','relation_kind','target_occurrence_id','locale'],
                                     'search':['locale','row_kind','container_id','owner_field']},
                'pagination_contract':{'scope':'exact run + contract + record kind + filters',
                                       'ordering':'physical occurrence id','max_limit':100,
                                       'payload_budget_bytes':262144,'partial_responses_explicit':True},
                'eligible_runs':[{'ingest_run_id':self.run,'source_snapshot':self.runtime.manifest['source_snapshot'],
                                  'binding_ingest_run_id':self.runtime.binding_run,'product_accepted':False}],
                'default_run':False,'latest_run':False,'semantic_relation_resolution':'mapping_unresolved',
                'candidate_search_complete':False,'physical_candidate_scan_complete':True,
                'current_7_0_registry_modified':False}

    def _result(self, data: dict, *, status: str='ok', run: str|None=None) -> FamilyEvidenceResult:
        return FamilyEvidenceResult(status,self.family,run or self.run,self.family_contract_version,self.coverage,True,data)

    def _check(self, run: str, contract: str) -> bool:
        return run==self.run and contract==self.family_contract_version

    def _missing(self, run: str) -> FamilyEvidenceResult:
        return self._result({'status':'NotFound'},status='not_found',run=run)

    @staticmethod
    def _wire_size(result: FamilyEvidenceResult) -> int:
        # Match the historical routed provider's conservative envelope budget.
        return 3*len(json.dumps(json.dumps(result.envelope(),ensure_ascii=True),ensure_ascii=True).encode())+8192

    def _bounded_get(self, data: dict) -> FamilyEvidenceResult:
        result=self._result(data)
        if self._wire_size(result)>262144:
            raise ValueError('single physical record exceeds inline limit; no truncation')
        return result

    @staticmethod
    def _filters(filters: dict|None, allowed: set[str]) -> dict:
        if filters is None:
            return {}
        if not isinstance(filters,dict) or any(key not in allowed or not isinstance(value,str) or not value for key,value in filters.items()):
            raise ValueError('unsupported or empty Review filter')
        return dict(sorted(filters.items()))

    def _page(self, rows: list[dict], scope: dict, limit: int) -> FamilyEvidenceResult:
        more=len(rows)>limit
        visible=rows[:limit]
        while visible and self._wire_size(self._result({'items':visible,'delivered_count':len(visible),
                       'next_cursor':self.runtime._cursor(scope,visible[-1]['id']) if more else None,
                       'scope_complete':not more,'partial':more}))>262144:
            visible.pop();more=True
        if rows and not visible:
            raise ValueError('single Review record exceeds 256 KiB; request exact get')
        return self._result({'items':visible,'delivered_count':len(visible),
                             'next_cursor':self.runtime._cursor(scope,visible[-1]['id']) if more and visible else None,
                             'scope_complete':not more,'partial':more})

    def list_evidence(self, *, ingest_run_id: str, family_contract_version: str,
                      filters: dict|None=None, cursor: str|None=None, limit: int=50) -> FamilyEvidenceResult:
        if not self._check(ingest_run_id,family_contract_version):
            return self._missing(ingest_run_id)
        if type(limit) is not int or not 1<=limit<=100:
            raise ValueError('limit 1..100')
        f=self._filters(filters,{'record_kind','row_kind','native_id','container_id','relation_kind','target_occurrence_id','locale'})
        kind=f.get('record_kind','rows')
        allowed={'rows':{'record_kind','row_kind','native_id'},
                 'relations':{'record_kind','container_id','relation_kind'},
                 'candidates':{'record_kind','container_id','target_occurrence_id'},
                 'text_slots':{'record_kind','container_id','locale'}}
        if kind not in allowed or set(f)-allowed[kind]:
            raise ValueError('Review record kind/filter mismatch')
        scope={'family':self.family,'run':self.run,'contract':self.family_contract_version,'operation':'list','kind':kind,'filters':f}
        after=self.runtime._after(cursor,scope)
        where=['id>?'];args=[after]
        if kind=='rows':
            sql='SELECT * FROM evidence_row WHERE '+(' AND '.join(where))
            if 'row_kind' in f:sql+=' AND kind=?';args.append(f['row_kind'])
            if 'native_id' in f:sql+=' AND native_id=?';args.append(f['native_id'])
            db=self.db
        elif kind=='relations':
            sql='SELECT * FROM bound_locator WHERE '+(' AND '.join(where))
            if 'container_id' in f:sql+=' AND source_review_row_id=?';args.append(f['container_id'])
            if 'relation_kind' in f:sql+=' AND role=?';args.append(f['relation_kind'])
            db=self.binding
        elif kind=='candidates':
            sql='SELECT * FROM bound_candidate WHERE '+(' AND '.join(where))
            if 'container_id' in f:sql+=' AND locator_id IN (SELECT id FROM bound_locator WHERE source_review_row_id=?)';args.append(f['container_id'])
            if 'target_occurrence_id' in f:sql+=' AND target_occurrence_id=?';args.append(f['target_occurrence_id'])
            db=self.binding
        else:
            sql='SELECT * FROM text_slot WHERE '+(' AND '.join(where))
            if 'container_id' in f:sql+=' AND row_id=?';args.append(f['container_id'])
            if 'locale' in f:sql+=' AND locale=?';args.append(f['locale'])
            db=self.db
        rows=[dict(row) for row in db.execute(sql+' ORDER BY id LIMIT ?',(*args,limit+1))]
        if kind=='relations':
            for row in rows:
                row.update({'semantic_mapping':'mapping_unresolved','logical_relation_closure':False})
        if kind=='candidates':
            for row in rows:
                row.update({'candidate_navigation':True,'logical_relation_closure':False})
        result=self._page(rows,scope,limit)
        return self._result({'record_kind':kind,**result.data})

    def get_evidence(self, *, ingest_run_id: str, family_contract_version: str,
                     occurrence_id: str) -> FamilyEvidenceResult:
        if not self._check(ingest_run_id,family_contract_version):
            return self._missing(ingest_run_id)
        if not isinstance(occurrence_id,str) or not occurrence_id:
            raise ValueError('exact occurrence ID required')
        row=self.db.execute('SELECT * FROM evidence_row WHERE id=?',(occurrence_id,)).fetchone()
        if row:
            exact=self.runtime.read({'family':self.family,'run':self.run,'id':occurrence_id})
            return self._bounded_get({'container':exact['container'],
                                 'counts':{'relations':self.binding.execute('SELECT count(*) FROM bound_locator WHERE source_review_row_id=?',(occurrence_id,)).fetchone()[0],
                                           'text_slots':self.db.execute('SELECT count(*) FROM text_slot WHERE row_id=?',(occurrence_id,)).fetchone()[0]},
                                 'bounded_record_kinds':['relations','candidates','text_slots'],'partial':True})
        relation=self.binding.execute('SELECT * FROM bound_locator WHERE id=?',(occurrence_id,)).fetchone()
        if relation:
            candidates=[dict(x) for x in self.binding.execute('SELECT * FROM bound_candidate WHERE locator_id=? ORDER BY id',(occurrence_id,))]
            return self._bounded_get({'relation':{**dict(relation),'logical_relation_closure':False},
                                 'candidates':candidates,'partial':False})
        slot=self.db.execute('SELECT * FROM text_slot WHERE id=?',(occurrence_id,)).fetchone()
        if slot:
            candidates=[dict(x) for x in self.db.execute('SELECT * FROM text_candidate WHERE slot_id=? ORDER BY id',(occurrence_id,))]
            return self._bounded_get({'text_slot':dict(slot),'text_candidates':candidates,'partial':False})
        return self._missing(ingest_run_id)

    def search_text(self, *, ingest_run_id: str, family_contract_version: str,
                    query: str, filters: dict|None=None, cursor: str|None=None,
                    limit: int=50) -> FamilyEvidenceResult:
        if not self._check(ingest_run_id,family_contract_version):
            return self._missing(ingest_run_id)
        if not isinstance(query,str) or not query or type(limit) is not int or not 1<=limit<=100:
            raise ValueError('query/limit')
        f=self._filters(filters,{'locale','row_kind','container_id','owner_field'})
        scope={'family':self.family,'run':self.run,'contract':self.family_contract_version,'operation':'search','query':query,'filters':f}
        after=self.runtime._after(cursor,scope)
        where=['c.id>?','instr(c.raw_text,?)>0'];args=[after,query]
        for key,column in (('locale','s.locale'),('row_kind','r.kind'),('container_id','r.id'),('owner_field','s.owner_field')):
            if key in f:where.append(column+'=?');args.append(f[key])
        sql='''SELECT c.id,c.slot_id,c.source_path,c.pair_ordinal,c.source_pointer,c.raw_text,c.raw_text_sha256,
                      s.locale,s.owner_field,r.id AS container_id,r.kind AS row_kind
               FROM text_candidate c JOIN text_slot s ON s.id=c.slot_id
               JOIN evidence_row r ON r.id=s.row_id WHERE '''+' AND '.join(where)+' ORDER BY c.id LIMIT ?'
        rows=[dict(row) for row in self.db.execute(sql,(*args,limit+1))]
        page=self._page(rows,scope,limit)
        return self._result({'literal_substring':True,**page.data})
