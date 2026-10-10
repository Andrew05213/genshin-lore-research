"""Low-level lexical source readers and pinned-byte checks for event archives."""
import hashlib,json,subprocess
from pathlib import Path
PIN='26df1dfbdf05a82bbb1d97506859f3e1c40718d8';TREE='d891ef1d95fdd6c27a1da8a32ea7a61b483647a6'
def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 return h.hexdigest()
def git_blob(path):
 p=Path(path);h=hashlib.sha1(b'blob '+str(p.stat().st_size).encode()+b'\0')
 with p.open('rb') as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 return h.hexdigest()
def array_records(path):
 text=Path(path).read_bytes().decode('utf-8');decoder=json.JSONDecoder();i=0
 while text[i].isspace():i+=1
 if text[i]!='[':raise ValueError('event source must be a physical JSON array')
 i+=1;ordinal=0
 while True:
  while text[i].isspace():i+=1
  if text[i]==']':
   if text[i+1:].strip():raise ValueError('trailing event source bytes')
   return
  value,end=decoder.raw_decode(text,i);yield ordinal,value,text[i:end];ordinal+=1;i=end
  while text[i].isspace():i+=1
  if text[i]==',':i+=1
  elif text[i]!=']':raise ValueError('event source array separator')
def pairs(path):
 value=json.loads(Path(path).read_bytes().decode('utf-8'),object_pairs_hook=lambda p:p)
 if not isinstance(value,list) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in value):raise ValueError('TextMap object of strings required')
 return value
def check_files(root,manifest):
 root=Path(root)
 if manifest['source_commit']!=PIN or manifest['source_tree']!=TREE or manifest['snapshot_id']!='anime-game-data:7.0:'+PIN:raise ValueError('event source pin')
 if subprocess.check_output(['git','-C',str(root),'rev-parse',PIN+'^{tree}'],text=True).strip()!=TREE:raise ValueError('event source tree mismatch')
 for item in manifest['files']:
  path=root/item['path'];blob=subprocess.check_output(['git','-C',str(root),'rev-parse',PIN+':'+item['path']],text=True).strip()
  if path.stat().st_size!=item['size_bytes'] or sha(path)!=item['sha256'] or git_blob(path)!=blob or blob!=item['blob']:raise ValueError('event actual bytes/pinned blob mismatch: '+item['path'])
