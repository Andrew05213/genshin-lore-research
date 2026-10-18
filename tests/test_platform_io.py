"""Cross-process platform behavior; no game sources or database fixtures."""
import os
from pathlib import Path
import subprocess
import sys

from genshin_lore.platform_io import lock_exclusive, unlock, peak_rss_bytes, sync_directory
from genshin_lore.artifact_io import write_json_exclusive
from genshin_lore.build_metrics import start_measurement, finish_measurement
from genshin_lore.batch_release import fsync_file
import pytest


def test_lock_excludes_another_process_and_unlocks(tmp_path):
    path = tmp_path/'stable.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    code = '''import os,sys
from genshin_lore.platform_io import lock_exclusive
fd=os.open(sys.argv[1],os.O_RDWR)
try:
    lock_exclusive(fd,blocking=False)
except OSError:
    raise SystemExit(3)
finally:
    os.close(fd)
'''
    try:
        lock_exclusive(fd)
        first = subprocess.run([sys.executable,'-c',code,str(path)],timeout=10)
        assert first.returncode == 3
        unlock(fd)
        second = subprocess.run([sys.executable,'-c',code,str(path)],timeout=10)
        assert second.returncode == 0
    finally:
        os.close(fd)


def test_publication_still_rejects_overwrite_and_syncs(tmp_path):
    path = tmp_path/'report.json'
    write_json_exclusive(path,{'status':'synthetic'})
    original=path.read_bytes()
    with pytest.raises(ValueError,match='already exists'):
        write_json_exclusive(path,{'status':'changed'})
    assert path.read_bytes()==original
    sync_directory(tmp_path)
    fsync_file(path)


def test_metrics_are_real_or_explicitly_unavailable():
    result=finish_measurement(start_measurement())
    assert result['cpu_user_seconds']>=0
    assert result['cpu_system_seconds']>=0
    peak=peak_rss_bytes()
    assert peak is None or peak>0
    assert result['peak_rss_bytes'] is None or result['peak_rss_bytes']>0


def test_platform_modules_import_without_posix_only_imports():
    from genshin_lore import batch_m1_textmap, narrative_7_1_build, quest_talk_7_1_build
    assert callable(narrative_7_1_build.build)
    assert callable(quest_talk_7_1_build.build)
    assert batch_m1_textmap._peak_rss_bytes()>0
