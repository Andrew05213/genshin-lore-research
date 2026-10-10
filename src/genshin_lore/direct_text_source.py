"""Mechanical source helpers for the two direct-text evidence families."""
from __future__ import annotations
import hashlib,json,subprocess
from pathlib import Path

COMMIT="26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
TREE="d891ef1d95fdd6c27a1da8a32ea7a61b483647a6"
SNAPSHOT="anime-game-data:7.0:"+COMMIT

def sha_bytes(raw:bytes)->str:return hashlib.sha256(raw).hexdigest()
def sha_file(path:Path)->str:
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
 return h.hexdigest()
def blob_oid(raw:bytes)->str:return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
def canonical(value)->str:return json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':'))
def ident(prefix,*parts)->str:return prefix+':'+hashlib.sha256(canonical(parts).encode()).hexdigest()[:40]
def pinned_tree(root:Path)->dict[str,str]:
 out=subprocess.check_output(['git','-C',str(root),'ls-tree','-r',COMMIT,'--','Readable','Subtitle'],text=True)
 return {line.split('\t',1)[1]:line.split()[2] for line in out.splitlines() if line}
def inventory(root:Path,tree_name:str)->list[dict]:
 pinned=pinned_tree(root);rows=[]
 for path in sorted((root/tree_name).rglob('*')):
  if not path.is_file():continue
  rel=path.relative_to(root).as_posix();raw=path.read_bytes();actual=blob_oid(raw)
  if pinned.get(rel)!=actual:raise ValueError('actual bytes do not match pinned Git blob: '+rel)
  rows.append({'path':rel,'size_bytes':len(raw),'sha256':sha_bytes(raw),'blob':actual})
 return rows
def implementation_seals(repo:Path,paths)->dict[str,str]:return {p:sha_file(repo/p) for p in paths}
def split_lines(raw:bytes):
 text=raw.decode('utf-8');offset=0
 for ordinal,part in enumerate(text.splitlines(keepends=True)):
  encoded=part.encode('utf-8');body=part.rstrip('\r\n');term=part[len(body):]
  yield ordinal,offset,offset+len(encoded),body,term
  offset+=len(encoded)

