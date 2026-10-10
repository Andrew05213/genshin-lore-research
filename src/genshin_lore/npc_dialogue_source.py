"""Pinned low-level source I/O for the NPC relation uplift."""
import hashlib,json,subprocess
from pathlib import Path
from .loading_tips_source import sha,array_records
ROOT=Path(__file__).resolve().parents[2]
COMMIT='26df1dfbdf05a82bbb1d97506859f3e1c40718d8'
TREE='d891ef1d95fdd6c27a1da8a32ea7a61b483647a6'
NPC='ExcelBinOutput/NpcExcelConfigData.json'
EXCEL=['ExcelBinOutput/TalkExcelConfigData_0.json','ExcelBinOutput/TalkExcelConfigData_1.json']
def canonical(x):return json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'))
def digest(x):return hashlib.sha256(canonical(x).encode()).hexdigest()
def document(root,path):return json.loads((Path(root)/path).read_bytes().decode('utf-8-sig'))
def dependencies(m):
 for p,s in m['dependencies'].items():
  if sha(ROOT/p)!=s:raise ValueError('frozen dependency drift: '+p)
def paths(m):
 result={NPC,*EXCEL}
 for b in m['talk_bundles']:
  s=json.loads((ROOT/b['scope']).read_text());result.update(s['program_source_files'])
  if b['kind']=='quest_linked':result.update(s['quest_source_files'])
 return sorted(result)
def source_seals(root,m):
 dependencies(m)
 if subprocess.check_output(['git','-C',str(root),'rev-parse',COMMIT+'^{tree}'],text=True).strip()!=TREE:raise ValueError('tree')
 if m['commit']!=COMMIT or m['tree']!=TREE:raise ValueError('pin')
 raw=subprocess.check_output(['git','-C',str(root),'ls-tree','-r','-z',COMMIT]);blobs={}
 for entry in raw.split(b'\0'):
  if entry:
   header,path=entry.decode().split('\t');mode,kind,blob=header.split();blobs[path]=(mode,kind,blob)
 out=[]
 for path in paths(m):
  p=Path(root)/path;raw=p.read_bytes();blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
  if blobs.get(path)!=('100644','blob',blob):raise ValueError('source bytes '+path)
  out.append((path,len(raw),hashlib.sha256(raw).hexdigest(),blob))
 if digest(out)!=m['source_seal']:raise ValueError('source denominator/seal')
 return out
