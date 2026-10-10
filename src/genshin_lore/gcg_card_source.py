"""Pinned file/byte reading for the GCG deck-card and card-face boundary."""
import hashlib,subprocess
from .loading_tips_source import sha,array_records,pairs
COMMIT='26df1dfbdf05a82bbb1d97506859f3e1c40718d8'
TREE='d891ef1d95fdd6c27a1da8a32ea7a61b483647a6'
PRIMARY=['ExcelBinOutput/GCGDeckCardExcelConfigData.json','ExcelBinOutput/GCGCardFaceExcelConfigData.json']
def check_files(root,m):
 expected=PRIMARY+[f'TextMap/TextMap{tier}{lang}.json' for lang in ('CHS','EN','JP') for tier in ('','_Medium')]
 if m['source_commit']!=COMMIT or m['source_tree']!=TREE or m['snapshot_id']!='anime-game-data:7.0:'+COMMIT or [x['path'] for x in m['files']]!=expected:raise ValueError('GCG frozen source boundary')
 if m['primary_rows']!=[619,1238] or m['contract']!='gcg-card-source-v1':raise ValueError('GCG scope contract')
 if subprocess.check_output(['git','-C',str(root),'rev-parse',COMMIT+'^{tree}'],text=True).strip()!=TREE:raise ValueError('source tree')
 for item in m['files']:
  p=root/item['path'];size=p.stat().st_size
  if size!=item['size_bytes'] or sha(p)!=item['sha256']:raise ValueError('source bytes')
  blob=subprocess.check_output(['git','-C',str(root),'rev-parse',COMMIT+':'+item['path']],text=True).strip();h=hashlib.sha1(b'blob '+str(size).encode()+b'\0')
  with p.open('rb') as f:
   for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
  if h.hexdigest()!=blob or blob!=item['blob']:raise ValueError('source path/blob')
