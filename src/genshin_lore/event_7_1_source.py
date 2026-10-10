"""Strict 7.1 physical source checks for bounded Event projections."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .event_narrative_source import array_records, git_blob, pairs, sha

COMMIT = '9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'
TREE = 'aa578c08c402af39b41f1ffc95124d10f5410272'
SNAPSHOT = 'anime-game-data:7.1:' + COMMIT


def check_files(root: Path, scope: dict) -> None:
    root = Path(root).resolve()
    if (scope['source_commit'], scope['source_tree'], scope['snapshot_id']) != (COMMIT, TREE, SNAPSHOT):
        raise ValueError('Event 7.1 source scope pin')
    if subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip() != COMMIT:
        raise ValueError('Event 7.1 checkout commit')
    if subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD^{tree}'], text=True).strip() != TREE:
        raise ValueError('Event 7.1 checkout tree')
    if subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], text=True).strip():
        raise ValueError('Event 7.1 source checkout dirty')
    for entry in scope['files']:
        path = root / entry['path']
        blob = subprocess.check_output(['git', '-C', str(root), 'rev-parse',
                                        COMMIT + ':' + entry['path']], text=True).strip()
        if (path.stat().st_size != entry['size_bytes'] or sha(path) != entry['sha256']
                or git_blob(path) != entry['blob'] or blob != entry['blob']):
            raise ValueError('Event 7.1 source bytes mismatch: ' + entry['path'])
