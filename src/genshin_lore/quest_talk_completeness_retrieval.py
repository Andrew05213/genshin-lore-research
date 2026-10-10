"""v5 multidimensional completeness and literal search on sealed covering indexes."""
import json
from copy import deepcopy
from .quest_talk_physical_retrieval import QuestTalkPhysicalProvider
from .r5_quest_talk_retrieval import _filters,_decode,_cursor
from .family_evidence import FamilyEvidenceResult

class QuestTalkCompleteProvider(QuestTalkPhysicalProvider):
    family_contract_version='quest-talk-evidence-v5'
    runtime_contract='quest-talk-completeness-runtime-v1'
    mapping_version='physical-completeness-v1'
    product_accepted=True
    binding_keys=('scope_manifest','shape_manifest','terminal_manifest')

    def capability(self):
        result=super().capability()
        result['coverage_semantics']['completeness']=deepcopy(self.manifest['completeness'])
        result['eligible_runs'][0]['completeness']=deepcopy(self.manifest['completeness'])
        result['eligible_runs'][0]['terminal_evidence']=deepcopy(self.manifest['terminal_manifest'])
        result['reference_delivery']='known physical candidates; physical lookup complete, semantic classification partial with terminal unresolved'
        return result

    def get_evidence(self,**args):
        result=super().get_evidence(**args)
        if result.status=='ok':result.data['completeness']=deepcopy(self.manifest['completeness'])
        return result

    def search_text(self,*,ingest_run_id,family_contract_version,query,filters=None,cursor=None,limit=50):
        if not self._check(ingest_run_id,family_contract_version):
            return FamilyEvidenceResult('not_found',self.family,ingest_run_id,family_contract_version,'unavailable',False,{'status':'NotFound'})
        if not isinstance(query,str) or not query:raise ValueError('query required')
        if type(limit) is not int or not 1<=limit<=1000:raise ValueError('limit out of range')
        f=_filters(filters,{'locale','native_id','source_path'})
        if 'locale' in f and f['locale'] not in ('CHS','EN','JP'):raise ValueError('invalid locale')
        scope={'run':ingest_run_id,'operation':'search','filters':f,'query':query};last=_decode(cursor,scope)
        clauses=['instr(t.raw_text,?)>0'];params=[query]
        if 'native_id' in f or 'source_path' in f:
            index='quest_talk_physical_search_path' if 'source_path' in f else 'quest_talk_physical_search_native'
            source=f'quest_talk_physical_container c INDEXED BY {index} CROSS JOIN quest_talk_physical_text t INDEXED BY quest_talk_physical_text_container'
            clauses.append('t.container_id=c.id')
            for name,col in [('native_id','c.native_id'),('source_path',"json_extract(c.metadata_json,'$.source_path')")]:
                if name in f:clauses.append(col+'=?');params.append(f[name])
        else:
            index='quest_talk_physical_search_locale' if 'locale' in f else 'quest_talk_physical_search_cover'
            source=f'quest_talk_physical_text t INDEXED BY {index}'
        if 'locale' in f:clauses.append('t.locale=?');params.append(f['locale'])
        if last:clauses.append('t.id>?');params.append(last)
        # Qualification guarantees one exact run. Candidate SQL reads only its
        # sealed artifact; final row fetch still enforces the explicit run.
        inner='SELECT t.id FROM '+source+' WHERE '+' AND '.join(clauses)+' ORDER BY t.id LIMIT ?'
        # Materialize the bounded ID set in Python: bound-parameter SQLite
        # plans can otherwise reorder the outer joins into a whole-row scan.
        hits=self.conn.execute(inner,(*params,limit+1)).fetchall()
        ids=[r[0] for r in hits]
        rows=[]
        if ids:
            sql='SELECT t.*,c.native_id,sf.relative_path AS source_path FROM quest_talk_physical_text t JOIN quest_talk_physical_container c ON c.id=t.container_id JOIN source_record sr ON sr.id=c.source_record_id JOIN source_file sf ON sf.id=sr.source_file_id WHERE t.id IN ('+','.join('?' for _ in ids)+') AND t.ingest_run_id=? ORDER BY t.id'
            rows=self.conn.execute(sql,(*ids,ingest_run_id)).fetchall()
        items=[dict(r) for r in rows[:limit]]
        return self._result('ok',{'items':items,'next_cursor':_cursor(scope,items[-1]['id']) if len(rows)>limit else None})
