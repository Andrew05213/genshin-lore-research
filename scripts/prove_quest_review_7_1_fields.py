#!/usr/bin/env python3
"""Full-denominator physical-field continuity proof; no cross-snapshot entity match."""
from __future__ import annotations
import argparse,json,subprocess,sys
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from genshin_lore.loading_tips_source import array_records,pairs,sha
OLD='26df1dfbdf05a82bbb1d97506859f3e1c40718d8';NEW='9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'
GROUP='ExcelBinOutput/QuestReviewExcelConfigData.json';TAB='ExcelBinOutput/QuestReviewTabGroupExcelConfigData.json'
FIELDS={GROUP:{'KHAPIAPJAHD':'GJHKONEAHPC','JIKNKEAFDJO':'CGPCOFBDIFP'},TAB:{'GIAIIFFDFNE':'CCIHLEICLMF','BGOCCKKGLPH':'PGLHIJCBJCE','PFDNJJNHAOJ':'LIIIELBGLEI','BOGKOENEJLM':'NJJJODOAICN'}}
def check(x,m):
 if not x:raise ValueError(m)
def seals(source,commit):
 return {path:{'sha256':sha(source/path),'git_blob':subprocess.check_output(['git','-C',str(source),'rev-parse',commit+':'+path],text=True).strip()} for path in FIELDS}
def proof(old,new):
 old=Path(old);new=Path(new)
 check(subprocess.check_output(['git','-C',str(old),'rev-parse','HEAD'],text=True).strip()==OLD,'old source pin')
 check(subprocess.check_output(['git','-C',str(new),'rev-parse','HEAD'],text=True).strip()==NEW,'new source pin')
 out={'contract':'quest-review-7.1-field-continuity-v1','old_source_commit':OLD,'new_source_commit':NEW,'old_source_seals':seals(old,OLD),'new_source_seals':seals(new,NEW),'alignment':'same physical row ordinal plus same source-owned id; physical comparison only, no logical identity','fields':{}}
 for path,mappings in FIELDS.items():
  older=[(i,value) for i,value,_ in array_records(old/path)];newer=[(i,value) for i,value,_ in array_records(new/path)]
  check(len(older)==len(newer),'denominator '+path)
  for left,right in zip(older,newer):
   check(left[0]==right[0] and left[1]['id']==right[1]['id'],'physical coordinate/id continuity '+path)
  records={}
  for old_field,new_field in mappings.items():
   present_old=sum(old_field in value for _,value in older);present_new=sum(new_field in value for _,value in newer)
   mismatches=[i for (i,o),(_,n) in zip(older,newer) if o.get(old_field)!=n.get(new_field)]
   check(not mismatches and present_old==present_new,'field values/presence '+path+'/'+old_field)
   type_counts=dict(Counter(type(value[new_field]).__name__ for _,value in newer if new_field in value))
   records[old_field]={'new_field':new_field,'rows':len(older),'old_present':present_old,'new_present':present_new,'mismatches':0,'value_type_counts':type_counts,'physical_order_preserved':True}
  out['fields'][path]=records
 group=[value for _,value in newer] if path==GROUP else None
 # Independent target-space role check: only GJHKONEAHPC is integer-valued review member array.
 review=[v for _,v,_ in array_records(new/GROUP)];tabs=[v for _,v,_ in array_records(new/TAB)]
 namespace=defaultdict(int)
 for row in review+tabs:namespace[row['id']]+=1
 member=[value for row in review for value in row.get('GJHKONEAHPC',[])]
 check(all(type(value) is int for value in member),'member locator primitive')
 check(all(isinstance(row['CGPCOFBDIFP'],list) and all(isinstance(value,dict) for value in row['CGPCOFBDIFP']) for row in review),'condition array distinguished from member array')
 out['target_space']={'review_member_locator_count':len(member),'candidate_cardinality':dict(Counter(namespace[x] for x in member)),'candidate_occurrences':sum(namespace[x] for x in member)}
 check(len(member)==11 and out['target_space']['candidate_cardinality']=={1:6,2:5},'member target-space behavior')
 owners=[value for row in tabs for value in row['CCIHLEICLMF']]
 check(len(owners)==28 and all(type(value) is int for value in owners),'tab text locator type')
 for field in ('LIIIELBGLEI','PGLHIJCBJCE'):
  check(all(isinstance(v,str) for row in tabs for v in row[field]),'resource-path field distinct '+field)
 check(all(isinstance(v,dict) for row in tabs for v in row['NJJJODOAICN']),'tab condition distinct')
 out['target_space']['tab_text_hash_locators']=28
 for language in ('CHS','EN','JP'):
  mapping={key for key,_ in pairs(new/f'TextMap/TextMap{language}.json')}
  out['target_space'][language+'_tab_text_main_hits']=sum(str(value) in mapping for value in owners)
 check([out['target_space'][l+'_tab_text_main_hits'] for l in ('CHS','EN','JP')]==[8,7,7],'TextMap target behavior')
 out['verdict']='PROVEN_PHYSICAL_FIELD_CONTINUITY_NOT_LOGICAL_IDENTITY'
 return out
def check_tracked(computed,tracked):
 check(computed['contract']==tracked['contract'] and computed['verdict']==tracked['verdict'],'tracked proof identity')
 check(computed['old_source_commit']==tracked['old_source_commit'] and computed['new_source_commit']==tracked['new_source_commit'],'tracked proof pins')
 check(computed['alignment']==tracked['alignment'],'tracked proof alignment')
 for path,entry in tracked['source_seals'].items():
  old=computed['old_source_seals'][path];new=computed['new_source_seals'][path]
  check((old['sha256'],old['git_blob'],new['sha256'],new['git_blob'])==(entry['old_sha256'],entry['old_git_blob'],entry['new_sha256'],entry['new_git_blob']),'tracked source seals '+path)
 check(set(tracked['source_seals'])==set(FIELDS),'tracked source file denominator')
 for path,mappings in tracked['field_continuity'].items():
  check(len(mappings)==len(FIELDS[path]),'tracked field denominator '+path)
  for entry in mappings:
   actual=computed['fields'][path][entry['old']]
   check(entry['new']==actual['new_field'] and entry['rows']==actual['rows'] and entry['present']==actual['old_present']==actual['new_present'] and entry['mismatches']==actual['mismatches']==0,'tracked field continuity '+path+'/'+entry['old'])
 check(set(tracked['field_continuity'])==set(FIELDS),'tracked family denominator')
 target=tracked['target_space_checks'];observed=computed['target_space']
 check(target['member_locator_count']==observed['review_member_locator_count'] and target['member_candidate_occurrences']==observed['candidate_occurrences'],'tracked member target space')
 check(target['member_candidate_cardinality']=={'one':observed['candidate_cardinality'].get(1,0),'two':observed['candidate_cardinality'].get(2,0)},'tracked member ambiguity')
 check(target['tab_text_locator_count']==observed['tab_text_hash_locators'],'tracked tab owner count')
 check(target['tab_text_main_hits']=={language:observed[language+'_tab_text_main_hits'] for language in ('CHS','EN','JP')},'tracked TextMap target behavior')
 return {'verdict':'PASS_TRACKED_FIELD_PROOF','fields':sum(map(len,FIELDS.values())),'source_files':len(FIELDS),'member_candidates':observed['candidate_occurrences'],'tab_text_locators':observed['tab_text_hash_locators']}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--old',required=True);p.add_argument('--new',required=True);p.add_argument('--tracked');a=p.parse_args();value=proof(a.old,a.new)
 print(json.dumps(check_tracked(value,json.loads(Path(a.tracked).read_text())) if a.tracked else value,indent=2,ensure_ascii=False,sort_keys=True))
