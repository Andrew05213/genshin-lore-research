"""Install a checkout's Windows research service without changing global Codex settings."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import uuid

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = 'https://github.com/DimbreathBot/AnimeGameData.git'
SOURCE = '9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'
TREE = 'aa578c08c402af39b41f1ffc95124d10f5410272'
HISTORY = '26df1dfbdf05a82bbb1d97506859f3e1c40718d8'
SERVER = 'genshin-lore-research71'
PEAK = 22_000_000_000


def run(args, *, capture=False):
    """Stream diagnostics to the installer log; never ignore a failed command."""
    print('+', subprocess.list2cmdline([str(a) for a in args]), flush=True)
    result = subprocess.run(
        [str(a) for a in args], check=True, text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture else None


def checkout(path, commit, tree=None):
    if not path.exists():
        path.mkdir(parents=True)
        run(['git', 'init', path])
        run(['git', '-C', path, 'config', 'core.autocrlf', 'false'])
        run(['git', '-C', path, 'remote', 'add', 'origin', UPSTREAM])
        run(['git', '-C', path, 'fetch', '--depth', '1', 'origin', commit])
        run(['git', '-C', path, 'checkout', '--detach', 'FETCH_HEAD'])
    # Existing or interrupted checkouts are never reset, cleaned or rewritten.
    actual = run(['git', '-C', path, 'rev-parse', '--show-toplevel'], capture=True)
    if Path(actual).resolve() != path.resolve():
        raise ValueError(f'Not a dedicated source checkout: {path}')
    if run(['git', '-C', path, 'rev-parse', 'HEAD'], capture=True) != commit:
        raise ValueError(f'Pinned source differs; preserved: {path}')
    if run(['git', '-C', path, 'status', '--porcelain', '--untracked-files=all'], capture=True):
        raise ValueError(f'Dirty source; preserved: {path}')
    if tree and run(['git', '-C', path, 'rev-parse', 'HEAD^{tree}'], capture=True) != tree:
        raise ValueError(f'Source tree differs: {path}')


def configure_codex(root, python, config):
    """Append one project server; refuse conflicts and preserve the original bytes."""
    path = root / '.codex' / 'config.toml'
    expected = {
        'command': str(python),
        'args': [str(root / 'scripts' / 'serve_dialogue.py'), '--config', str(config)],
        'enabled': True, 'required': True,
        'startup_timeout_sec': 90, 'tool_timeout_sec': 60,
    }
    old = path.read_bytes() if path.exists() else b''
    parsed = tomllib.loads(old.decode('utf-8-sig'))
    existing = parsed.get('mcp_servers', {}).get(SERVER)
    if existing is not None:
        if existing != expected:
            raise ValueError(f'Existing MCP entry differs; not overwritten: {path}')
        return path
    lines = ['\n', f'[mcp_servers.{SERVER}]']
    for key, value in expected.items():
        lines.append(f'{key} = {json.dumps(value, ensure_ascii=False)}')
    suffix = ('\n'.join(lines) + '\n').encode('utf-8')
    # JSON string/array syntax is valid for these TOML values, including Windows paths.
    tomllib.loads((old + suffix).decode('utf-8-sig'))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f'Config symlink is not changed: {path}')
    if path.exists():
        backup = path.with_name(f'config.toml.before-setup-{uuid.uuid4().hex}.bak')
        with backup.open('xb') as stream:
            stream.write(old)
    temporary = path.with_name(f'.setup-{uuid.uuid4().hex}.toml')
    with temporary.open('xb') as stream:
        stream.write(old + suffix)
    if (path.read_bytes() if path.exists() else b'') != old:
        raise ValueError('Config changed during setup; new draft preserved.')
    os.replace(temporary, path)
    return path


def headroom(root):
    # Reuse the same ownership-aware accounting and policy as the builders.
    from genshin_lore.batch_space import broad_inventory
    from genshin_lore.storage_policy import PROJECT_HARD_LIMIT_BYTES

    current = broad_inventory(root)
    remaining = PROJECT_HARD_LIMIT_BYTES - current.allocated_bytes
    free = shutil.disk_usage(root).free
    print(f'Project allocated: {current.allocated_bytes}; budget remaining: {remaining}; disk free: {free}', flush=True)
    if remaining < PEAK or free < PEAK + 1_000_000_000:
        raise ValueError('Full build requires 22 GB project headroom and 23 GB disk free after setup inputs. No data deleted.')
    return remaining


def install(root, *, jobs=2):
    os.chdir(root)
    os.environ['PYTHONUTF8'] = '1'
    os.environ['PYTHONPATH'] = str(root / 'src')
    # Keep the installer's dependency download cache inside its measured project.
    os.environ['PIP_CACHE_DIR'] = str(root / 'cache' / 'pip')
    python = root / '.venv' / 'Scripts' / 'python.exe'
    if not python.exists():
        run([sys.executable, '-m', 'venv', root / '.venv'])
    run([python, '-c', 'import sys; sys.exit(0 if sys.version_info[:2] == (3,12) else 1)'])
    # hnswlib needs numpy/pybind11 at metadata/build time, before pip installs
    # its resolved runtime dependencies. Use the same pinned order as bundle CI.
    run([python, '-m', 'pip', 'install', '--require-hashes', '-r', 'requirements-wheel-build.lock'])
    run([python, '-m', 'pip', 'install', '--no-build-isolation', '--require-hashes',
         '-r', 'requirements-tools.lock'])
    run([python, '-m', 'pip', 'install', '--no-deps', '--no-build-isolation', '-e', '.'])
    # Dispatch after dependencies are installed, without installing into the system Python.
    if Path(sys.executable).resolve() != python.resolve():
        run([python, root / 'scripts' / 'setup_windows.py', '--prepared', '--logged', '--jobs', jobs])
        return
    finish(root, python, jobs)


def finish(root, python, jobs):
    source = root / 'data' / 'AnimeGameData'
    historical = root / 'data' / 'AnimeGameData-history'
    output = root / 'data' / 'rebuilt'
    cache = root / 'data' / 'model-cache'
    config = output / 'runtime-config.json'
    headroom(root)
    checkout(source, SOURCE, TREE)
    checkout(historical, HISTORY)
    run([python, root / 'scripts' / 'maintenance' / 'fetch_semantic_model.py', '--cache-root', cache])
    # Always enter the existing coordinator: it alone qualifies stage reuse.
    run([python, root / 'scripts' / 'build.py', '--source', source,
         '--historical-source', historical, '--output-root', output,
         '--model-cache', cache, '--available-allocated-bytes', headroom(root),
         '--stage', 'all', '--jobs', jobs])
    run([python, root / 'scripts' / 'serve_dialogue.py', '--config', config, '--check'])
    run([python, root / 'scripts' / 'check_dialogue.py', '--config', config])
    path = configure_codex(root, python, config)
    print(f'\nSETUP COMPLETE. Project MCP config: {path}\nOpen/trust this project in Codex, restart its MCP connection, and check /mcp.\nThis verifies data and MCP, not autonomous model answer quality.\nNative Codex does not automatically include the project citation renderer.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, choices=(1, 2), default=2)
    parser.add_argument('--prepared', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--logged', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Run setup.cmd on native Windows; this does not configure WSL.')
    if not args.logged:
        logs = ROOT / 'data' / 'setup-logs'
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.log')
        print(f'Installing; live log: {log}', flush=True)
        # Tee bytes without interpreting or rewriting child output.
        with log.open('xb') as stream:
            process = subprocess.Popen([sys.executable, __file__, *sys.argv[1:], '--logged'],
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            for line in process.stdout:
                stream.write(line)
                stream.flush()
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()
            return process.wait()
    try:
        if args.prepared:
            finish(ROOT, Path(sys.executable).resolve(), args.jobs)
        else:
            install(ROOT, jobs=args.jobs)
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'SETUP FAILED: {error}\nExisting sources, reports and data preserved.\nIf hnswlib compilation failed, install MSVC C++ Build Tools and retry.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
