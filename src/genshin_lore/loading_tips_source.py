"""Low-level sealed source reading; no LoadingTips field/resolution decisions."""
import hashlib,json,subprocess
from pathlib import Path

def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 return h.hexdigest()

def array_records(path):
 text=Path(path).read_bytes().decode('utf-8');decoder=json.JSONDecoder();i=0
 while text[i].isspace():i+=1
 if text[i]!='[':raise ValueError('expected array')
 i+=1;ordinal=0
 while True:
  while text[i].isspace():i+=1
  if text[i]==']':
   if text[i+1:].strip():raise ValueError('trailing bytes')
   return
  value,end=decoder.raw_decode(text,i)
  yield ordinal,value,text[i:end]
  ordinal+=1;i=end
  while text[i].isspace():i+=1
  if text[i]==',':i+=1
  elif text[i]!=']':raise ValueError('array separator')

def pairs(path):
 value=json.loads(Path(path).read_bytes().decode('utf-8'),object_pairs_hook=lambda p:p)
 if not isinstance(value,list) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in value):raise ValueError('TextMap object of strings required')
 return value

def check_files(root,manifest):
 commit='26df1dfbdf05a82bbb1d97506859f3e1c40718d8'
 tree='d891ef1d95fdd6c27a1da8a32ea7a61b483647a6'
 if manifest['source_commit']!=commit or manifest['source_tree']!=tree or manifest['snapshot_id']!='anime-game-data:7.0:'+commit:raise ValueError('LoadingTips source pin')
 if subprocess.check_output(['git','-C',str(root),'rev-parse',commit+'^{tree}'],text=True).strip()!=tree:raise ValueError('source tree mismatch')
 expected=['ExcelBinOutput/LoadingTipsExcelConfigData.json']+[f'TextMap/TextMap{tier}{lang}.json' for lang in ('CHS','EN','JP') for tier in ('','_Medium')]
 if [x['path'] for x in manifest['files']]!=expected or manifest['primary_rows']!=1004 or manifest['text_fields']!=['tipsTitleTextMapHash','tipsDescTextMapHash'] or manifest['languages']!=['CHS','EN','JP']:raise ValueError('LoadingTips frozen boundary')
 for item in manifest['files']:
  p=root/item['path']
  if p.stat().st_size!=item['size_bytes'] or sha(p)!=item['sha256']:raise ValueError('source seal mismatch: '+item['path'])
  blob=subprocess.check_output(['git','-C',str(root),'rev-parse',commit+':'+item['path']],text=True).strip()
  h=hashlib.sha1(b'blob '+str(p.stat().st_size).encode()+b'\0')
  with p.open('rb') as f:
   for block in iter(lambda:f.read(1048576),b''):h.update(block)
  if h.hexdigest()!=blob or blob!=item['blob']:raise ValueError('source path/blob mismatch')
