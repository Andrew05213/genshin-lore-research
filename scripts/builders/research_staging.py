"""Private disk policy and durable diagnostics for the Research writer.

This limits SQLite caches and database pages, not Python/oracle total RAM.
SQLite temporary work uses the system temporary volume; its free space and
the compaction reservation are recorded independently of project headroom.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

from genshin_lore.batch_release import (
    assert_output_paths, create_private_staging, fsync_file, publish_absent,
)

CACHE_KIB = 16 * 1024
TEMP_CACHE_KIB = 8 * 1024
RESERVE_BYTES = 16 * 1024**2


def write_json(path, value):
    """Exclusive, UTF-8 and file-fsynced; never replace an earlier observation."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def connect(path, budget):
    if not isinstance(budget, int) or budget <= RESERVE_BYTES + 2 * 65536:
        raise ValueError('Research staging budget is insufficient')
    db = sqlite3.connect(path)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA page_size=4096')
        db.execute(f'PRAGMA max_page_count={(budget-RESERVE_BYTES)//(2*4096)}')
        db.execute(f'PRAGMA cache_size=-{CACHE_KIB}')
        db.execute('PRAGMA temp_store=FILE')
        db.execute(f'PRAGMA temp.cache_size=-{TEMP_CACHE_KIB}')
        db.execute('PRAGMA mmap_size=0')
        db.execute('PRAGMA cache_spill=ON')
        return db
    except BaseException:
        db.close()
        raise


def database_bytes(db):
    return db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]


def reserve_compaction(db, path, budget):
    size = database_bytes(db)
    temporary = Path(tempfile.gettempdir())
    # Original schema uses journal_mode=OFF. Keep that policy: a rollback
    # journal containing another full image would require a different budget.
    if db.execute('PRAGMA journal_mode').fetchone()[0] != 'off':
        raise ValueError('Research compaction journal policy drift')
    required = 2 * size + RESERVE_BYTES
    observation = {'database_bytes': size, 'reserved_peak_bytes': required,
        'available_allocated_bytes': budget, 'fits': required <= budget,
        'output_volume_free_bytes': shutil.disk_usage(path.parent).free,
        'system_temp_volume_free_bytes': shutil.disk_usage(temporary).free,
        'temporary_volume': str(temporary.anchor),
        'limit': 'Two database images plus reserve; SQLite/Python caches are not a whole-process RAM bound.'}
    if required > budget:
        raise ValueError(f'Research compaction budget: {required} B exceeds {budget} B')
    if min(observation['output_volume_free_bytes'], observation['system_temp_volume_free_bytes']) < size + RESERVE_BYTES:
        raise ValueError('Research compaction volume space is insufficient')
    return observation


def compact(db):
    db.execute('VACUUM')


def scene(path, output):
    return [{'path': str(p), 'size_bytes': p.stat().st_size}
        for base in (path, output)
        for p in (base, *(Path(str(base)+s) for s in ('-wal','-shm','-journal')))
        if p.is_file()]


class Diagnostics:
    def __init__(self, directory, stem):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory/f'index-staging-{stem}.jsonl'
        self.stream = self.path.open('x', encoding='utf-8', newline='\n')
        self.started = time.perf_counter()

    def record(self, phase, **details):
        self.stream.write(json.dumps({'phase': phase,
            'elapsed_seconds': time.perf_counter()-self.started, **details},
            ensure_ascii=False, sort_keys=True)+'\n')
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def close(self):
        self.stream.close()


def create(output, budget, inputs):
    if not isinstance(budget, int) or budget <= RESERVE_BYTES + 2 * 65536:
        raise ValueError('Research staging budget is insufficient')
    if any(os.path.lexists(p) for p in
            (output, *(Path(str(output)+s) for s in ('-wal','-shm','-journal')))):
        raise FileExistsError(output)
    assert_output_paths(output, inputs=inputs)
    fd, path = create_private_staging(output, prefix='.research-')
    os.close(fd)
    return path


def publish(output, path, inputs):
    fsync_file(path)
    publish_absent(output, path, inputs=inputs)
