"""Low-level source reading and exact Character Lore Wave bindings."""
import hashlib,json,subprocess
from pathlib import Path
from .loading_tips_source import sha,array_records,pairs

COMMIT='26df1dfbdf05a82bbb1d97506859f3e1c40718d8'
TREE='d891ef1d95fdd6c27a1da8a32ea7a61b483647a6'
SNAPSHOT='anime-game-data:7.0:'+COMMIT
COSTUME='ExcelBinOutput/AvatarCostumeExcelConfigData.json'
PROFILE='ExcelBinOutput/ProfilePictureExcelConfigData.json'
RENAME='ExcelBinOutput/AvatarRenameExcelConfigData.json'
AVATAR='ExcelBinOutput/AvatarExcelConfigData.json'
LANGUAGES=('CHS','EN','JP')
FILES=[COSTUME,PROFILE,RENAME,AVATAR]+[f'TextMap/TextMap{tier}{lang}.json' for lang in LANGUAGES for tier in ('','_Medium')]
C5B_RUN='ir-character-phase4c5b:38849b74a42fdaf24ed8e1aff9bca5b070534d76'
WAVE02_RUN='ir-batch-m5c-wave02:b033d33c48801376269ecd4e8e9b7c347e71e379'
PROFILE_REUSED_ORDINALS=(0,1,150,151,184)

def canonical(value):return json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'))
def ident(*parts):return 'character-lore:'+hashlib.sha256(canonical(parts).encode()).hexdigest()[:40]
def git_blob(root,path):return subprocess.check_output(['git','-C',str(root),'rev-parse',COMMIT+':'+path],text=True).strip()
def file_seal(root,path):
 p=Path(root)/path;size=p.stat().st_size;blob=git_blob(root,path);h=hashlib.sha1(b'blob '+str(size).encode()+b'\0')
 with p.open('rb') as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 if h.hexdigest()!=blob:raise ValueError('Git blob mismatch '+path)
 return {'path':path,'size_bytes':size,'sha256':sha(p),'blob':blob}
def check_scope(root,m):
 root=Path(root)
 if m['contract']!='character-lore-scope-v1' or m['source_commit']!=COMMIT or m['source_tree']!=TREE or m['snapshot_id']!=SNAPSHOT:raise ValueError('source identity')
 if subprocess.check_output(['git','-C',str(root),'rev-parse',COMMIT+'^{tree}'],text=True).strip()!=TREE:raise ValueError('source tree')
 if [x['path'] for x in m['files']]!=FILES or m['denominators']!={'costume_rows':160,'profile_rows':186,'profile_reused_rows':5,'profile_new_rows':181,'rename_rows':3,'avatar_lookup_rows':165}:raise ValueError('frozen denominator')
 if m['profile_reused_ordinals']!=list(PROFILE_REUSED_ORDINALS) or m['c5b_run_id']!=C5B_RUN or m['wave02_run_id']!=WAVE02_RUN:raise ValueError('accepted reuse binding')
 for expected in m['files']:
  if file_seal(root,expected['path'])!=expected:raise ValueError('source drift '+expected['path'])
