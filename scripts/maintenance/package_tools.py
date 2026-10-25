"""Package tracked source and a platform wheelhouse, never generated data.

The resulting archive is a source-checkout distribution, not a deployed corpus.
Wheel hashes describe these actual platform builds, including hnswlib's native
wheel; the upstream lock remains the recipe for reproducing their inputs.
"""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[2]


def normalized(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def package(root, wheelhouse, output):
    root, wheelhouse, output = map(lambda p: Path(p).resolve(), (root, wheelhouse, output))
    subprocess.run(['git', '-C', str(root), 'diff', '--exit-code', 'HEAD', '--'],
                   check=True, stdout=subprocess.DEVNULL)
    commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    tracked = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z']).decode().split('\0')
    entries = {}
    allowed = {'.py', '.sql', '.json', '.md', '.toml', '.txt', '.lock', '.yml', '.yaml', '.ini'}
    for name in filter(None, tracked):
        path = root/name
        if (path.is_symlink() or not path.is_file()
                or (path.suffix not in allowed and name not in {'LICENSE', 'NOTICE', '.gitignore'})):
            raise ValueError('unsupported tracked source entry: '+name)
        entries['source/'+name] = path.read_bytes()
    pins = {}
    for filename in ('requirements-tools.lock', 'requirements-wheel-build.lock'):
        for name, version in re.findall(r'^([\w.-]+)==([^\s\\]+)', (root/filename).read_text(), re.M):
            key = normalized(name)
            if key in pins and pins[key] != version:
                raise ValueError('conflicting tool dependency lock: '+key)
            pins[key] = version
    wheels = []
    for path in sorted(wheelhouse.iterdir()):
        if path.is_symlink() or not path.is_file() or path.suffix != '.whl':
            raise ValueError('wheelhouse must contain only platform wheels: '+path.name)
        raw = path.read_bytes()
        with ZipFile(path) as wheel:
            metadata = [n for n in wheel.namelist() if n.endswith('.dist-info/METADATA')]
            if len(metadata) != 1 or wheel.testzip() is not None:
                raise ValueError('invalid wheel: '+path.name)
            info = BytesParser().parsebytes(wheel.read(metadata[0]))
        name, version = normalized(info['Name']), info['Version']
        if pins.get(name) != version or any(w['name'] == name for w in wheels):
            raise ValueError('unexpected, duplicate or unpinned wheel: '+path.name)
        wheels.append({'name': name, 'version': version, 'file': path.name,
                       'sha256': hashlib.sha256(raw).hexdigest(),
                       'license': info.get('License-Expression') or info.get('License'),
                       'license_files': info.get_all('License-File', [])})
        entries['wheelhouse/'+path.name] = raw
    if not wheels:
        raise ValueError('empty wheelhouse')
    # A lock resolves platform markers during pip wheel. Install only the wheels
    # selected for that platform; offline installation still resolves dependencies.
    entries['install.lock'] = ''.join(
        f"{w['name']}=={w['version']} --hash=sha256:{w['sha256']}\n" for w in wheels).encode()
    manifest = {'contract': 'source-and-dependencies-v1', 'source_commit': commit,
                'python': platform.python_version(), 'platform': platform.platform(),
                'machine': platform.machine(), 'wheels': wheels,
                'excluded': ['game data', 'databases', 'models', 'embeddings', 'vector indexes'],
                'files': {n: {'size_bytes': len(v), 'sha256': hashlib.sha256(v).hexdigest()}
                          for n, v in sorted(entries.items())}}
    entries['bundle.json'] = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)+'\n').encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream, ZipFile(stream, 'w', compression=ZIP_DEFLATED) as archive:
        for name, raw in sorted(entries.items()):
            archive.writestr(name, raw)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheelhouse', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = package(ROOT, args.wheelhouse, args.output)
    print(json.dumps({'source_commit': result['source_commit'], 'wheels': len(result['wheels']),
                      'files': len(result['files']), 'output': str(args.output)}))


if __name__ == '__main__':
    main()
