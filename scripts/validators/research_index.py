#!/usr/bin/env python3
"""Independent accepted-artifact reconstruction checks for research index v2.

This verifier never calls build_index or its row iterators.  It independently
checks every derived container/text/relation/candidate coordinate against the
accepted source artifact and independently closes every adapter denominator.
"""
import argparse,hashlib,json,sqlite3,time
from pathlib import Path
from genshin_lore.research_index import sha256_file
from genshin_lore.research_adapters import CORE_PROJECTED_PREFIXES,CORE_SPECIAL,RELATIONS,UNSUPPORTED_RELATION_TABLES,declared_tables,declared_relation_tables,relation_like_tables

EXPECTED_COLUMNS={
 'corpus_release':('id','manifest_sha256','corpus_ref','adapter_registry','built_at','source_count','container_count','text_count','relation_count','candidate_count'),
 'source_artifact':('source_key','artifact_family','artifact_contract','artifact_parser','artifact_run','path','size_bytes','sha256'),
 'adapter_coverage':('source_key','adapter_id','projection_kind','source_table','denominator','indexed_count','status','detail'),
 'research_container':('id','source_key','adapter_id','evidence_family','evidence_contract','evidence_parser','evidence_run','occurrence_id','container_kind','source_table','pk_column','pk_value','source_path','source_pointer','source_file_sha256','source_git_blob','ordinal','ordering_basis'),
 'research_text':('id','container_id','source_key','adapter_id','evidence_family','evidence_contract','evidence_parser','evidence_run','occurrence_id','child_kind','child_id','source_table','pk_column','pk_value','text_column','locale','owner_field','ordinal','pointer','text_source_path','text_source_pointer','text_source_file_sha256','text_source_git_blob','physical_pair_ordinal','raw_text','text_sha256'),
 'research_relation':('id','container_id','source_key','adapter_id','source_table','pk_column','pk_value','relation_kind','source_pointer','raw_locator','status','search_complete','candidate_count','candidate_namespace'),
 'research_candidate':('id','relation_id','source_table','pk_column','pk_value','target_family','target_contract','target_parser','target_run','target_occurrence_id','target_source_path','target_pointer','raw_target'),
}
EXPECTED_INDEXES={'research_container_identity','research_container_path','research_text_scope','research_text_container','research_text_locale','research_relation_source','research_candidate_target'}

def require(condition, message):
 if not condition:raise ValueError('research verification failed: '+str(message))


def verify_schema(db):
 actual=tables(db);allowed=set(EXPECTED_COLUMNS)|{'research_text_fts','research_text_fts_data','research_text_fts_idx','research_text_fts_docsize','research_text_fts_config'}
 require(actual==allowed, (sorted(actual-allowed),sorted(allowed-actual)))
 for table,want in EXPECTED_COLUMNS.items():
  info=list(db.execute(f'pragma table_info("{table}")'));require(tuple(x[1] for x in info)==want, 'research_index.py:28 validation condition failed')
  sql=db.execute("select sql from sqlite_master where type='table' and name=?",(table,)).fetchone()[0].lower()
  require('primary key' in sql, 'research_index.py:30 validation condition failed')
  if table in ('source_artifact','adapter_coverage'):require('without rowid' in sql, 'research_index.py:31 validation condition failed')
  if table=='adapter_coverage':require("'complete','unsupported','not_declared','duplicate_projection'" in sql.replace(' ',''), 'research_index.py:32 validation condition failed')
 indexes={r[0] for r in db.execute("select name from sqlite_master where type='index' and name not like 'sqlite_%'")};require(EXPECTED_INDEXES<=indexes, 'research_index.py:33 validation condition failed')
 require(not list(db.execute("select name from sqlite_master where type='trigger'")), 'research_index.py:34 validation condition failed')
 fts=db.execute("select sql from sqlite_master where name='research_text_fts'").fetchone()[0].lower();require("content='research_text'" in fts and "trigram case_sensitive 1" in fts, 'research_index.py:35 validation condition failed')
 required_fk={('research_container','source_artifact'),('research_text','research_container'),('research_relation','research_container'),('research_candidate','research_relation')}
 actual_fk={(t,r[2]) for t in EXPECTED_COLUMNS for r in db.execute(f'pragma foreign_key_list("{t}")')};require(required_fk<=actual_fk, 'research_index.py:37 validation condition failed')
 return {'authoritative_tables':sorted(EXPECTED_COLUMNS),'indexes':sorted(EXPECTED_INDEXES),'unexpected_authoritative_objects':[]}

def normalized(db):return [(r[0],r[1],r[2],' '.join((r[3] or '').lower().split())) for r in db.execute("select type,name,tbl_name,sql from sqlite_master where name not like 'sqlite_%' order by type,name")]
def tables(db):return {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
def cols(db,t):return {r[1] for r in db.execute(f'pragma table_info("{t}")')}
def expected_adapter_tables(src,family):
 ts=tables(src);want=set()
 if family!='schema20_core':
  declared=declared_tables(family)
  for kind,names in declared.items():
   projection={'containers':'container','texts':'text','relations':'relation','candidates':'candidate'}[kind]
   want.update((projection,t) for t in names if t in ts)
 else:
  for prefix in CORE_PROJECTED_PREFIXES:
   for projection,t in (('container',prefix+'_row_occurrence'),('text',prefix+'_text_candidate_occurrence')):
    if t in ts:want.add((projection,t))
  for spec in CORE_SPECIAL.values():
   if spec['row'] in ts:want.add(('container',spec['row']))
   if spec.get('text') in ts:want.add(('text',spec['text']))
  for projection,t in (('container','passage_occurrence'),('text','passage_occurrence_text'),('container','static_evidence_container'),('text','static_segment_occurrence'),('text','static_text_candidate_occurrence')):
   if t in ts:want.add((projection,t))
 # Every source text table must be classified, including deliberate mirrors.
 for t in ts:
   if t!='textmap_physical_occurrence' and 'raw_text' in cols(src,t) and src.execute(f'select count(*) from "{t}" where raw_text is not null').fetchone()[0]:want.add(('text',t))
 for t in relation_like_tables(src,family):
  want.add(('candidate' if 'candidate' in t else 'relation',t))
 return want
def explicit_text_tables(src,family):
 ts=tables(src)
 if family!='schema20_core':return {t for t in declared_tables(family)['texts'] if t in ts}
 out={prefix+'_text_candidate_occurrence' for prefix in CORE_PROJECTED_PREFIXES if prefix+'_text_candidate_occurrence' in ts}
 out.update(spec['text'] for spec in CORE_SPECIAL.values() if spec.get('text') in ts)
 out.update(t for t in ('passage_occurrence_text','static_segment_occurrence','static_text_candidate_occurrence') if t in ts)
 return out
def seal(db,t):
 h=hashlib.sha256()
 for row in db.execute(f'select * from {t} order by '+('id' if 'id' in cols(db,t) else ','.join('"'+r[1]+'"' for r in db.execute(f'pragma table_info("{t}")') if r[5]))):h.update((json.dumps(list(row),ensure_ascii=False,separators=(',',':'))+'\n').encode())
 return h.hexdigest()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--corpus',type=Path,required=True);ap.add_argument('--bindings',type=Path,required=True);ap.add_argument('--index',type=Path,required=True);ap.add_argument('--report',type=Path,required=True);a=ap.parse_args();started=time.perf_counter();m=json.loads(a.corpus.read_text());b=json.loads(a.bindings.read_text());root=a.corpus.resolve().parents[2]
 db=sqlite3.connect(f'file:{a.index}?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row;schema_proof=verify_schema(db)
 release=db.execute('select * from corpus_release where id=1').fetchone();require(release and release['manifest_sha256']==hashlib.sha256(a.corpus.read_bytes()).hexdigest() and release['corpus_ref']==m['corpus_ref'] and release['adapter_registry']=='research-explicit-adapters-v2', 'research_index.py:79 validation condition failed')
 prov=Path(b['provenance_artifact']);require(prov.stat().st_size==m['provenance_dependency']['size_bytes'] and sha256_file(prov)==m['provenance_dependency']['sha256'], 'research_index.py:80 validation condition failed');r0db=sqlite3.connect(f'file:{prov}?mode=ro&immutable=1',uri=True);r0={str(p):str(s) for p,s in r0db.execute('select relative_path,source_sha256 from source_file')};r0db.close()
 components={x['source_key']:x for x in m['components']};sources={};verified={'container':0,'text':0,'relation':0,'candidate':0}
 for key,x in components.items():
  path=Path(b['artifacts'][key]);require(path.stat().st_size==x['artifact']['size_bytes'] and sha256_file(path)==x['artifact']['sha256'], 'research_index.py:83 validation condition failed');require(sha256_file(root/x['runtime_manifest'])==x['runtime_manifest_sha256'], 'research_index.py:83 validation condition failed');got=db.execute('select artifact_family,artifact_contract,artifact_parser,artifact_run,size_bytes,sha256 from source_artifact where source_key=?',(key,)).fetchone();require(tuple(got)==(x['family'],x['contract'],x['parser'],x['run'],x['artifact']['size_bytes'],x['artifact']['sha256']), 'research_index.py:83 validation condition failed');src=sqlite3.connect(f'file:{path}?mode=ro&immutable=1',uri=True);src.row_factory=sqlite3.Row;sources[key]=src
  actual={(r[0],r[1]) for r in db.execute('select projection_kind,source_table from adapter_coverage where source_key=?',(key,))};expected=expected_adapter_tables(src,x['family']);require(expected==actual, (key,sorted(expected-actual),sorted(actual-expected)));require(relation_like_tables(src,x['family'])<=declared_relation_tables(x['family']), 'research_index.py:84 validation condition failed')
 # Full container coordinate/evidence identity/provenance reconstruction.
 for row in db.execute('select * from research_container order by id'):
  src=sources[row['source_key']];require(row['source_table'] in tables(src), 'research_index.py:87 validation condition failed');got=src.execute(f'select * from "{row["source_table"]}" where "{row["pk_column"]}"=?',(row['pk_value'],)).fetchone();require(got is not None, 'research_index.py:87 validation condition failed');gc=set(got.keys())
  if 'source_path' in gc:require(str(got['source_path'])==row['source_path'], 'research_index.py:88 validation condition failed')
  if 'source_file_path' in gc:require(str(got['source_file_path'])==row['source_path'], 'research_index.py:89 validation condition failed')
  if 'pointer' in gc:require(str(got['pointer'])==row['source_pointer'], 'research_index.py:90 validation condition failed')
  if 'source_pointer' in gc:require(str(got['source_pointer'])==row['source_pointer'], 'research_index.py:91 validation condition failed')
  if 'family' in gc:
   require((row['evidence_family'],row['evidence_contract'],row['evidence_parser'],row['evidence_run'])==(str(got['family']),str(got['family_contract_version']),str(got['parser_build_id']),str(got['ingest_run_id'])), 'research_index.py:93 validation condition failed')
  elif row['source_key']!='schema20-core':
   x=components[row['source_key']];require((row['evidence_family'],row['evidence_contract'],row['evidence_parser'],row['evidence_run'])==(x['family'],x['contract'],x['parser'],x['run']), 'research_index.py:95 validation condition failed')
  elif 'ingest_run_id' in gc:
   ir=src.execute('select parser_build_id,scope_json from ingest_run where id=?',(got['ingest_run_id'],)).fetchone();require(ir, 'research_index.py:97 validation condition failed');scope=json.loads(ir[1]);contract=src.execute('select parser_version from parser_build where id=?',(ir[0],)).fetchone()[0];require((row['evidence_family'],row['evidence_contract'],row['evidence_parser'],row['evidence_run'])==(str(scope.get('family',row['evidence_family'])),str(contract),str(ir[0]),str(got['ingest_run_id'])), 'research_index.py:97 validation condition failed')
  proven=False
  for sc in ('source_sha256','source_file_sha256','sha256'):
   if sc in gc and got[sc]:require(str(got[sc])==row['source_file_sha256'], 'research_index.py:100 validation condition failed');proven=True;break
  if not proven and row['source_path'] in r0:require(r0[row['source_path']]==row['source_file_sha256'], 'research_index.py:101 validation condition failed');proven=True
  if not proven and 'source_record_id' in gc and 'source_file' in tables(src):
   q=src.execute('select f.sha256 from source_record r join source_file f on f.id=r.source_file_id where r.id=?',(got['source_record_id'],)).fetchone();require(q and q[0]==row['source_file_sha256'], 'research_index.py:103 validation condition failed');proven=True
  require(proven, 'research_index.py:104 validation condition failed');verified['container']+=1
 # Full text equality, child mapping and provenance tuple reconstruction.
 for row in db.execute('select * from research_text order by id'):
  src=sources[row['source_key']]
  if row['adapter_id']=='codexquest:text_candidate':got=src.execute('select o.* from codexquest_text_candidate c join codexquest_text_occurrence o on o.id=c.text_occurrence_id where c.id=?',(row['pk_value'],)).fetchone()
  else:got=src.execute(f'select * from "{row["source_table"]}" where "{row["pk_column"]}"=?',(row['pk_value'],)).fetchone()
  require(got is not None and row['text_column'] in got.keys(), 'research_index.py:110 validation condition failed');raw=str(got[row['text_column']]);require(raw==row['raw_text'] and hashlib.sha256(raw.encode()).hexdigest()==row['text_sha256'], 'research_index.py:110 validation condition failed')
  for field,value in (('pointer',row['text_source_pointer']),('source_pointer',row['text_source_pointer']),('locale',row['locale']),('pair_ordinal',row['physical_pair_ordinal'])):
   if field in got.keys() and value is not None:require(str(got[field])==str(value), 'research_index.py:112 validation condition failed')
  for field in ('source_sha256','source_file_sha256','raw_text_sha256'):
   if field in got.keys() and got[field] is not None and field!='raw_text_sha256':require(str(got[field])==row['text_source_file_sha256'], 'research_index.py:114 validation condition failed')
  if row['text_source_path'] in r0:require(r0[row['text_source_path']]==row['text_source_file_sha256'], 'research_index.py:115 validation condition failed')
  if row['text_source_path'].startswith(('Readable/','Subtitle/')):
   bits=row['text_source_path'].split('/');known=('CHS','CHT','DE','EN','ES','FR','ID','IT','JP','KR','PT','RU','TH','TR','VI');require(row['locale']==(bits[1] if bits[1] in known else None), 'research_index.py:117 validation condition failed')
  parent=db.execute('select * from research_container where id=?',(row['container_id'],)).fetchone();require(parent and tuple(row[x] for x in ('source_key','evidence_family','evidence_contract','evidence_parser','evidence_run','occurrence_id'))==tuple(parent[x] for x in ('source_key','evidence_family','evidence_contract','evidence_parser','evidence_run','occurrence_id')), 'research_index.py:118 validation condition failed');verified['text']+=1
 # Full relation state and candidate source coordinates.
 for row in db.execute('select * from research_relation order by id'):
  src=sources[row['source_key']];got=src.execute(f'select * from "{row["source_table"]}" where "{row["pk_column"]}"=?',(row['pk_value'],)).fetchone();require(got, 'research_index.py:121 validation condition failed')
  gc=set(got.keys());kind=got['relation_kind'] if 'relation_kind' in gc else got['kind'] if 'kind' in gc else row['relation_kind'];ptr=got['source_pointer'] if 'source_pointer' in gc else got['origin_pointer'] if 'origin_pointer' in gc else got['pointer'] if 'pointer' in gc else row['source_pointer'];require(str(kind)==row['relation_kind'] and str(ptr)==row['source_pointer'] and str(got['status'])==row['status'] and int(got['candidate_count'])==row['candidate_count'], 'research_index.py:122 validation condition failed')
  if 'search_complete' in gc:require(int(got['search_complete'])==row['search_complete'], 'research_index.py:123 validation condition failed')
  for name in ('candidate_namespace','target_namespace','boundary'):
   if name in gc and got[name] is not None:require(str(got[name])==row['candidate_namespace'], 'research_index.py:125 validation condition failed');break
  for name in ('raw_locator','raw_locator_json','raw_value','raw_value_json'):
   if name in gc and got[name] is not None:require(str(got[name])==row['raw_locator'], 'research_index.py:127 validation condition failed');break
  verified['relation']+=1
 for row in db.execute('select c.*,r.source_key from research_candidate c join research_relation r on r.id=c.relation_id order by c.id'):
  src=sources[row['source_key']];got=src.execute(f'select * from "{row["source_table"]}" where "{row["pk_column"]}"=?',(row['pk_value'],)).fetchone();require(got, 'research_index.py:130 validation condition failed');gc=set(got.keys())
  aliases={'target_family':('target_family',),'target_contract':('target_contract',),'target_parser':('target_parser','target_parser_build_id'),'target_run':('target_run','target_run_id'),'target_occurrence_id':('target_occurrence_id',),'target_source_path':('target_source_path',),'target_pointer':('target_source_pointer','target_pointer')}
  for projected,names in aliases.items():
   for name in names:
    if name in gc and got[name] is not None:require(str(got[name])==str(row[projected]), 'research_index.py:134 validation condition failed');break
  if 'npc_id' in gc:require(str(got['npc_id'])==row['raw_target'], 'research_index.py:135 validation condition failed')
  verified['candidate']+=1
 # Independent denominator closure; a synced derived count cannot hide deletion.
 for row in db.execute('select * from adapter_coverage order by source_key,projection_kind,source_table'):
  src=sources[row['source_key']];require(row['source_table'] in tables(src), 'research_index.py:139 validation condition failed');c=cols(src,row['source_table']);den=src.execute(f'select count(*) from "{row["source_table"]}"'+(' where raw_text is not null' if row['projection_kind']=='text' and 'raw_text' in c else '')).fetchone()[0];require(den==row['denominator'], 'research_index.py:139 validation condition failed')
  target={'container':'research_container','text':'research_text','relation':'research_relation','candidate':'research_candidate'}[row['projection_kind']];indexed=db.execute(f'select count(*) from {target} where source_key=? and source_table=?',(row['source_key'],row['source_table'])).fetchone()[0] if target!='research_candidate' else db.execute('select count(*) from research_candidate c join research_relation r on r.id=c.relation_id where r.source_key=? and c.source_table=?',(row['source_key'],row['source_table'])).fetchone()[0]
  family=components[row['source_key']]['family'];expected_indexed=den
  if row['projection_kind']=='text' and row['source_table'] not in explicit_text_tables(src,family):expected_indexed=0
  if row['projection_kind'] in ('relation','candidate') and row['source_table'] in UNSUPPORTED_RELATION_TABLES.get(family,()):expected_indexed=0
  elif row['projection_kind']=='relation':
   spec=next((x for x in RELATIONS.get(family,()) if x['table']==row['source_table']),None);require(spec is not None, 'research_index.py:145 validation condition failed')
   parents={r[0] for r in db.execute('select occurrence_id from research_container where source_key=?',(row['source_key'],))};expected_indexed=sum(1 for x in src.execute(f'select "{spec["parent"]}" from "{row["source_table"]}"') if str(x[0]) in parents)
  if row['source_table'] not in UNSUPPORTED_RELATION_TABLES.get(family,()) and row['projection_kind']=='candidate':
   spec=next((x for x in RELATIONS.get(family,()) if x['candidate']==row['source_table']),None);require(spec is not None, 'research_index.py:148 validation condition failed')
   indexed_relations={r[0] for r in db.execute('select pk_value from research_relation where source_key=? and source_table=?',(row['source_key'],spec['table']))};expected_indexed=sum(1 for x in src.execute(f'select relation_id from "{row["source_table"]}"') if str(x[0]) in indexed_relations)
  require(indexed==row['indexed_count']==expected_indexed, 'research_index.py:150 validation condition failed')
  explicitly_unsupported=row['source_table'] in UNSUPPORTED_RELATION_TABLES.get(family,())
  expected_status='unsupported' if explicitly_unsupported or expected_indexed!=den else 'complete'
  require(row['status']==expected_status or (row['status']=='duplicate_projection' and expected_indexed==0), (row['source_key'],row['projection_kind'],row['source_table'],row['status'],expected_status,den,expected_indexed))
 # FTS and release counts are independently closed.
 require(db.execute('select count(*) from research_text_fts').fetchone()[0]==db.execute('select count(*) from research_text').fetchone()[0], 'research_index.py:155 validation condition failed')
 counts={t:db.execute(f'select count(*) from {t}').fetchone()[0] for t in ('source_artifact','adapter_coverage','research_container','research_text','research_relation','research_candidate')};require((release['source_count'],release['container_count'],release['text_count'],release['relation_count'],release['candidate_count'])==(counts['source_artifact'],counts['research_container'],counts['research_text'],counts['research_relation'],counts['research_candidate']), 'research_index.py:156 validation condition failed')
 seals={t:seal(db,t) for t in ('adapter_coverage','research_container','research_text','research_relation','research_candidate')};unsupported=[dict(r) for r in db.execute("select source_key,projection_kind,source_table,denominator,detail from adapter_coverage where status='unsupported' order by source_key,projection_kind,source_table")]
 for s in sources.values():s.close()
 zero_text=counts['research_container']-db.execute('select count(distinct container_id) from research_text').fetchone()[0];db.close()
 # FTS5's rank=1 integrity-check independently compares every posting with the
 # external content table.  The command is rolled back and byte identity is
 # checked before/after, so verification cannot mutate the derived artifact.
 before_sha=sha256_file(a.index);rw=sqlite3.connect(a.index);rw.execute('begin');rw.execute("insert into research_text_fts(research_text_fts,rank) values('integrity-check',1)");rw.rollback();rw.close();require(sha256_file(a.index)==before_sha, 'research_index.py:163 validation condition failed');require(not any(Path(str(a.index)+s).exists() for s in ('-wal','-shm','-journal')), 'research_index.py:163 validation condition failed')
 out={'status':'pass','independent_source_reconstruction':True,'full_business_row_equality':True,'no_extra_rows':True,'schema_structure_match':True,'schema_proof':schema_proof,'adapter_denominators_independent':True,'relation_registry_complete':True,'fts_external_content_integrity_match':True,'evidence_identity_match':True,'provenance_tuple_match':True,'relation_state_match':True,'zero_text_containers_verified':zero_text,'verified_rows':verified,'counts':counts,'logical_seals':seals,'unsupported_adapters':unsupported,'artifact':{'size_bytes':a.index.stat().st_size,'sha256':before_sha},'seconds':time.perf_counter()-started};a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n');print(json.dumps(out))
if __name__=='__main__':main()
