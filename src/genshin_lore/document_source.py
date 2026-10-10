"""Only byte reading and source seals shared by Document builder and verifier."""
import hashlib,json,subprocess
from pathlib import Path
from .loading_tips_source import sha,array_records,pairs
COMMIT='26df1dfbdf05a82bbb1d97506859f3e1c40718d8'
TREE='d891ef1d95fdd6c27a1da8a32ea7a61b483647a6'
PRIMARY='ExcelBinOutput/DocumentExcelConfigData.json'
LOCALIZATION='ExcelBinOutput/LocalizationExcelConfigData.json'
LANGUAGES=('CHS','EN','JP')
SUPPORT=[LOCALIZATION,'ExcelBinOutput/DocumentLocalizationFormatExcelConfigData.json','ExcelBinOutput/DynamicBookExcelConfigData.json','ExcelBinOutput/DynamicBookDetailsExcelConfigData.json']
FILES=[PRIMARY]+SUPPORT+[f'TextMap/TextMap{tier}{lang}.json' for lang in LANGUAGES for tier in ('','_Medium')]
def readable_inventory(root):
 raw=subprocess.check_output(['git','-C',str(root),'ls-tree','-r','-z',COMMIT,'--',*[f'Readable/{x}/' for x in LANGUAGES]])
 result=[]
 for entry in raw.split(b'\0'):
  if not entry:continue
  header,path=entry.decode().split('\t');mode,kind,blob=header.split()
  if mode!='100644' or kind!='blob':raise ValueError('Readable physical file shape')
  p=Path(root)/path;size=p.stat().st_size;h=hashlib.sha1(b'blob '+str(size).encode()+b'\0');s=hashlib.sha256()
  with p.open('rb') as f:
   for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk);s.update(chunk)
  if h.hexdigest()!=blob:raise ValueError('Readable source drift')
  result.append({'path':path,'size_bytes':size,'sha256':s.hexdigest(),'blob':blob})
 return result
def inventory_seal(rows):return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def check_files(root,m):
 if m['contract']!='document-source-v1' or m['source_commit']!=COMMIT or m['source_tree']!=TREE or m['snapshot_id']!='anime-game-data:7.0:'+COMMIT or [x['path'] for x in m['files']]!=FILES or m['primary_rows']!=1852:raise ValueError('Document source boundary')
 if subprocess.check_output(['git','-C',str(root),'rev-parse',COMMIT+'^{tree}'],text=True).strip()!=TREE:raise ValueError('source pin')
 for x in m['files']:
  p=Path(root)/x['path'];size=p.stat().st_size
  if size!=x['size_bytes'] or sha(p)!=x['sha256']:raise ValueError('source seal')
  blob=subprocess.check_output(['git','-C',str(root),'rev-parse',COMMIT+':'+x['path']],text=True).strip();h=hashlib.sha1(b'blob '+str(size).encode()+b'\0')
  with p.open('rb') as f:
   for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
  if h.hexdigest()!=blob or blob!=x['blob']:raise ValueError('source Git binding')
 inv=readable_inventory(root)
 if len(inv)!=m['readable_lookup']['files'] or inventory_seal(inv)!=m['readable_lookup']['sha256']:raise ValueError('Readable denominator')
 return inv
