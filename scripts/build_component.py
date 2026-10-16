"""Portable source rebuild entry for the explicitly documented component set."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
COMMIT = '9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'
TREE = 'aa578c08c402af39b41f1ffc95124d10f5410272'


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            result.update(block)
    return result.hexdigest()


def qualify(source):
    values = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()
    dirty = subprocess.check_output(['git','-C',str(source),'status','--porcelain','--untracked-files=all'],text=True).strip()
    if values != [COMMIT,TREE] or dirty:
        raise ValueError('clean pinned upstream commit/tree required; no latest/fallback')


def components():
    contract = json.loads((ROOT/'manifests/m3-7.1-provider/input-contract-v1.json').read_text())
    return [x['family'] for x in contract['families']] + ['character_foundation','narrative_npc','narrative_dialog','narrative_reminder']


def write(path, value):
    with Path(path).open('x') as stream:
        json.dump(value,stream,ensure_ascii=False,sort_keys=True,indent=2)
        stream.write('\n')


def compare(left,right):
    results=[]
    for directory in (Path(left),Path(right)):
        verification=json.loads((directory/'verify.json').read_text())
        build=json.loads((directory/'build.json').read_text())
        if verification.get('status') != 'PASS':
            raise ValueError('both arms require independent verification PASS')
        artifact=directory/'artifact.sqlite'
        seal={'size_bytes':artifact.stat().st_size,'sha256':sha(artifact)}
        if build.get('artifact') != seal:
            raise ValueError('artifact differs from its build seal')
        results.append({'artifact':seal,'component':json.loads((directory/'invocation.json').read_text())['component'],
                        'build':build})
    if results[0]!=results[1]:
        raise ValueError('fresh A/B seal, component or deterministic build report mismatch')
    return {'status':'PASS','fresh_arm_seals_match':True,'artifact':results[0]['artifact']}


def rebuild(source,component,output_root):
    source=Path(source).resolve()
    output_root=Path(output_root).resolve()
    qualify(source)
    output_root.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(output_root).free < 3_000_000_000:
        raise ValueError('at least 3 GB host headroom required before component build; full build peak is larger')
    directory=output_root/component
    directory.mkdir()  # no-clobber; failures remain available for diagnosis
    artifact=directory/'artifact.sqlite'
    write(directory/'invocation.json',{'component':component,'source_commit':COMMIT,'source_tree':TREE,
                                      'local_build':True,'formal_release_accepted':False})
    if component=='character_foundation':
        from genshin_lore.character_7_1_foundation_build import build
        from genshin_lore.character_7_1_foundation_verify import verify
        scope=ROOT/'manifests/character-7.1-physical-foundation/source-scope.json'
        report=build(source,scope,artifact)
        write(directory/'build.json',report)
        verification=verify(source,scope,artifact,expected_artifact=report['artifact'])
    elif component.startswith('narrative_'):
        from genshin_lore.narrative_7_1_build import build
        from genshin_lore.narrative_7_1_verify import verify
        report=build(component.removeprefix('narrative_'),source,artifact,ROOT)
        write(directory/'build.json',report)
        verification=verify(source,artifact,report)
        # The narrative oracle declares its result as verdict, unlike the
        # other component oracles. Preserve it and expose the common envelope.
        verification={**verification,'status':verification.get('verdict')}
    else:
        from genshin_lore.m3_7_1_provider_build import build
        from genshin_lore.m3_7_1_provider_verify import verify
        report=build(source,component,artifact)
        write(directory/'build.json',report)
        verification=verify(source,component,artifact,directory/'build.json')
    write(directory/'verify.json',verification)
    if verification.get('status') != 'PASS':
        raise ValueError('independent verification did not PASS')
    return {'status':'PASS','component':component,'artifact':report['artifact'],
            'verification':'independent source/artifact check','formal_release_accepted':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--list',action='store_true')
    parser.add_argument('--component',choices=components())
    parser.add_argument('--output-root',type=Path,default=ROOT/'data/rebuilt')
    parser.add_argument('--compare',nargs=2,type=Path,metavar=('ARM_A','ARM_B'))
    args=parser.parse_args()
    if args.compare:
        result=compare(*args.compare)
    elif args.list:
        result={'portable_components':components(),'full_private_release_rebuild':False}
    else:
        if args.source is None:parser.error('--source is required; no auto-discovery of private inputs')
        qualify(args.source)
        if args.check:result={'status':'PASS','source_commit':COMMIT,'source_tree':TREE}
        elif args.component:result=rebuild(args.source,args.component,args.output_root)
        else:parser.error('choose --check, --list, --component or --compare')
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))


if __name__=='__main__':
    try:main()
    except (ValueError,FileExistsError,OSError,subprocess.CalledProcessError) as exc:
        raise SystemExit('Rebuild failed; outputs preserved, no fallback: '+str(exc))
