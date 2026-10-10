"""v7 reading contract over immutable v6 evidence; no new ingest identity."""
from dataclasses import replace
from copy import deepcopy
import json
from pathlib import Path
from .quest_talk_immutable_retrieval import QuestTalkImmutableProvider
from .quest_talk_physical_retrieval import _reviewed_manifest_digests
from .r5_quest_talk_retrieval import _fingerprint, _identity, _filters, _cursor, _decode, R5QuestTalkError
from .family_evidence import FamilyEvidenceResult

CONTRACT = 'quest-talk-evidence-v7'
BUDGET = 262144  # UTF-8 envelope JSON; wire escaping separately bounded below.
TABLES = {'nodes': ('quest_talk_physical_node', 'ordinal'),
          'texts': ('quest_talk_physical_text', 'node_id,locale'),
          'references': ('quest_talk_physical_reference', 'pointer')}


def encoded_size(value):
    # MCP serializes the envelope into a text-content JSON string. Count both
    # layers conservatively (ASCII escapes), with 4 KiB JSON-RPC overhead.
    return 2 * len(json.dumps(json.dumps(value, ensure_ascii=True), ensure_ascii=True).encode()) + 8192


class QuestTalkBoundedProvider:
    family = 'quest_talk_pilot'
    family_contract_version = CONTRACT

    def __init__(self, artifact_path, runtime_manifest_path):
        self.runtime_manifest_path = Path(runtime_manifest_path).absolute()
        fp = _fingerprint(self.runtime_manifest_path)
        declaration = json.loads(self.runtime_manifest_path.read_text())
        if fp[4] not in _reviewed_manifest_digests() or declaration['contract'] != 'quest-talk-bounded-runtime-v1' or declaration['family_contract_version'] != CONTRACT:
            raise R5QuestTalkError('unreviewed bounded contract')
        base = self.runtime_manifest_path.parent / declaration['evidence_manifest']['path']
        if _fingerprint(base)[4] != declaration['evidence_manifest']['sha256']:
            raise R5QuestTalkError('bounded evidence manifest binding')
        self.base = QuestTalkImmutableProvider(artifact_path, base)
        self._binding = fp[:4]
        self._invalidated = False
        self.manifest = self.base.manifest
        self.ingest_run_id = self.base.ingest_run_id
        if declaration['ingest_run_id'] != self.ingest_run_id or declaration['artifact'] != self.manifest['artifact']:
            raise R5QuestTalkError('bounded exact artifact/run binding')
        self.conn = self.base.conn
        self._assert_stable()

    def _assert_stable(self):
        try:
            if self._invalidated or _identity(self.runtime_manifest_path) != self._binding:
                raise R5QuestTalkError('bounded declaration drift')
            self.base._assert_stable()
        except (OSError, R5QuestTalkError):
            self._invalidated = True
            raise R5QuestTalkError('bounded run invalidated')

    def _result(self, data, status='ok'):
        self._assert_stable()
        return FamilyEvidenceResult(status,self.family,self.ingest_run_id,CONTRACT,self.base.coverage,False,data)

    def _check(self, run, contract):
        self._assert_stable()
        return run == self.ingest_run_id and contract == CONTRACT

    def _missing(self, run=None):
        return replace(self._result({'status':'NotFound'},'not_found'), ingest_run_id=run or self.ingest_run_id)

    def _budget(self, result):
        self._assert_stable()
        if encoded_size(result.envelope()) > BUDGET:
            raise ValueError('record_exceeds_inline_limit: evidence unchanged; explicit full:container locator remains available with documented large-client requirement')
        return result

    def capability(self):
        x = deepcopy(self.base.capability())
        x['family_contract_version'] = CONTRACT
        x['supported_filters']['list'] += ['container_id','record_kind','child_id']
        x['pagination_contract'] = {'ordering':'nodes ordinal; texts node_id,locale; references pointer', 'scope':'exact run + container + kind + filters + contract', 'wire_budget_bytes':BUDGET, 'oversized_record':'explicit error; no truncation', 'get':'container ID gives summary; JSON array [child,kind,container,child_id] gives exact row; full:<container> explicitly opts into frozen full get'}
        return x

    def _parent(self, container):
        row=self.conn.execute('SELECT c.*,sr.pointer,sf.relative_path AS source_path,sf.sha256 AS source_sha256,sf.size_bytes AS source_size_bytes FROM quest_talk_physical_container c JOIN source_record sr ON sr.id=c.source_record_id JOIN source_file sf ON sf.id=sr.source_file_id WHERE c.id=? AND c.ingest_run_id=?',(container,self.ingest_run_id)).fetchone()
        return dict(row) if row else None

    def list_evidence(self, *, ingest_run_id, family_contract_version, filters=None, cursor=None, limit=50):
        if not self._check(ingest_run_id,family_contract_version):return self._missing(ingest_run_id)
        f=_filters(filters,{'kind','native_id','source_path','container_id','record_kind','child_id'})
        if 'container_id' not in f:
            if set(f)&{'record_kind','child_id'}:raise ValueError('child scope requires exact container_id')
            scope={'contract':CONTRACT,'run':ingest_run_id,'operation':'containers','filters':f}
            inner=_decode(cursor,scope)
            r=self.base.list_evidence(ingest_run_id=ingest_run_id,family_contract_version=self.base.family_contract_version,filters=f,cursor=inner,limit=limit)
            if r.data.get('next_cursor'):r.data['next_cursor']=_cursor(scope,r.data['next_cursor'])
            return self._budget(replace(r,family_contract_version=CONTRACT))
        if set(f)-{'container_id','record_kind','child_id'}:raise ValueError('container child filters cannot mix with container selection filters')
        kind=f.get('record_kind')
        if kind not in TABLES:raise ValueError('record_kind must be nodes, texts or references')
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('limit must be 1..100')
        parent=self._parent(f['container_id'])
        if parent is None:return self._missing()
        scope={'contract':CONTRACT,'run':ingest_run_id,'operation':'children','filters':f}
        position=_decode(cursor,scope)
        offset=0 if position is None else int(position)
        if offset<0:raise ValueError('invalid position')
        table,order=TABLES[kind]
        where='container_id=? AND ingest_run_id=?';args=[f['container_id'],ingest_run_id]
        if 'child_id' in f:where+=' AND id=?';args.append(f['child_id'])
        total=self.conn.execute('SELECT count(*) FROM '+table+' WHERE '+where,args).fetchone()[0]
        if offset>total:raise ValueError('cursor beyond container')
        rows=self.conn.execute('SELECT * FROM '+table+' WHERE '+where+' ORDER BY '+order+' LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
        data={'partial':True,'record_kind':kind,'parent':parent,'snapshot_id':self.manifest['snapshot_id'],'parser_build_id':self.manifest['parser_build_id'],'items':[],'total':total,'offset':offset,'delivered_count':0,'next_cursor':None,'scope_complete':False}
        for row in rows:
            item=dict(row)
            # The locator contains opaque source-derived occurrence IDs, not native IDs.
            item={'evidence':item,'get_locator':json.dumps(['child',kind,f['container_id'],item['id']],separators=(',',':'))}
            trial=deepcopy(data);trial['items'].append(item);trial['delivered_count']=len(trial['items']);trial['next_cursor']=_cursor(scope,str(offset+len(trial['items'])))
            if encoded_size(self._result(trial).envelope())>BUDGET:
                if not data['items']:raise ValueError('record_exceeds_inline_limit: use explicit full container retrieval; no evidence truncated')
                break
            data=trial
        end=offset+len(data['items']);data['scope_complete']=end==total
        data['next_cursor']=None if end==total else _cursor(scope,str(end))
        return self._budget(self._result(data))

    def get_evidence(self, *, ingest_run_id, family_contract_version, occurrence_id):
        if not self._check(ingest_run_id,family_contract_version):return self._missing(ingest_run_id)
        if occurrence_id.startswith('full:'):
            r=self.base.get_evidence(ingest_run_id=ingest_run_id,family_contract_version=self.base.family_contract_version,occurrence_id=occurrence_id[5:])
            self._assert_stable()
            return replace(r,family_contract_version=CONTRACT)
        if occurrence_id.startswith('['):
            try:mode,kind,parent,child=json.loads(occurrence_id)
            except (ValueError,TypeError):raise ValueError('invalid child locator')
            if mode!='child':raise ValueError('invalid child locator')
            r=self.list_evidence(ingest_run_id=ingest_run_id,family_contract_version=CONTRACT,filters={'container_id':parent,'record_kind':kind,'child_id':child},limit=1)
            return r if r.status!='ok' or r.data['items'] else self._missing()
        parent=self._parent(occurrence_id)
        if parent is None:return self._missing()
        totals={k:self.conn.execute('SELECT count(*) FROM '+t+' WHERE container_id=?',(occurrence_id,)).fetchone()[0] for k,(t,_) in TABLES.items()}
        return self._budget(self._result({'partial':True,'record_kind':'container_summary','container':parent,'totals':totals,'snapshot_id':self.manifest['snapshot_id'],'parser_build_id':self.manifest['parser_build_id'],'full_get_locator':'full:'+occurrence_id}))

    def search_text(self, **args):
        if not self._check(args['ingest_run_id'],args['family_contract_version']):return self._missing(args['ingest_run_id'])
        scope={'contract':CONTRACT,'run':args['ingest_run_id'],'operation':'search','filters':args.get('filters'),'query':args['query']}
        args['cursor']=_decode(args.get('cursor'),scope)
        args['family_contract_version']=self.base.family_contract_version
        r=self.base.search_text(**args)
        if r.data.get('next_cursor'):r.data['next_cursor']=_cursor(scope,r.data['next_cursor'])
        self._assert_stable()
        return replace(r,family_contract_version=CONTRACT)
