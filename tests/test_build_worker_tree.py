"""Real worker -> verifier -> writer lifetime, on Linux and native Windows."""
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from scripts import build, build_workers


def running(pid):
    if os.name == 'nt':
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.OpenProcess.restype=ctypes.c_void_p
        api.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
        api.WaitForSingleObject.restype=ctypes.c_uint32
        api.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
        api.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=api.OpenProcess(0x00100000,False,pid)  # SYNCHRONIZE
        if not handle:
            if ctypes.get_last_error() in (87,1168):return False
            raise ctypes.WinError(ctypes.get_last_error())
        try:return api.WaitForSingleObject(handle,0)!=0
        finally:api.CloseHandle(handle)
    try:
        state=(Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()[0]
        return state not in ('Z','X')
    except FileNotFoundError:return False


def wait_for(path):
    end=time.monotonic()+10
    while not path.exists():
        if time.monotonic()>end:raise TimeoutError('synthetic descendant did not start')
        time.sleep(.01)


@pytest.mark.parametrize('failure,ignore_term', [('parent',False),('parent',True),('worker',False)])
@pytest.mark.parametrize('jobs',[2,3])
def test_failure_waits_for_nested_verifier_and_writer(tmp_path,monkeypatch,failure,ignore_term,jobs):
    root=tmp_path/'implementation';(root/'scripts').mkdir(parents=True)
    output=tmp_path/'output';output.mkdir()
    leaf='''import os,signal,sys,time
from pathlib import Path
out=Path(sys.argv[1])
if sys.argv[2]=='True' and os.name!='nt':signal.signal(signal.SIGTERM,signal.SIG_IGN)
(out/'leaf.pid').write_text(str(os.getpid()))
(out/'ready').touch()
time.sleep(3)
(out/'late-output').write_text('synthetic late write')
'''
    verifier='''import os,subprocess,sys
from pathlib import Path
out=Path(sys.argv[1]);(out/'verifier.pid').write_text(str(os.getpid()))
assert sys.stdin.read()==''
subprocess.run([sys.executable,'-c',sys.argv[3],str(out),sys.argv[2]],check=True)
'''
    script='''import argparse,os,subprocess,sys,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output-root');p.add_argument('--stage');a,_=p.parse_known_args()
out=Path(a.output_root)/a.stage;out.mkdir();(out/'worker.pid').write_text(str(os.getpid()))
child=subprocess.Popen([sys.executable,'-c',VERIFIER,str(out),IGNORE,LEAF])
if FAILURE=='worker':
 while not (out/'ready').exists():time.sleep(.01)
 os._exit(7)
child.wait()
'''
    (root/'scripts/build.py').write_text('VERIFIER='+repr(verifier)+'\nLEAF='+repr(leaf)+
        '\nIGNORE='+repr(str(ignore_term))+'\nFAILURE='+repr(failure)+'\n'+script)
    failed_at=[]
    worker_names=('r0',) if jobs==2 else ('r0','quest')
    def core():
        for name in worker_names:wait_for(output/name/'ready')
        failed_at.append(time.monotonic())
        if failure=='parent':raise ValueError('synthetic parent failure')
    def unexpected(*args):pytest.fail('continued to a consumer after failure')
    job=SimpleNamespace(output=output,source=tmp_path/'source',history=None,
        core=core,r0=unexpected,readable=unexpected,dependency_inputs=unexpected)
    monkeypatch.setattr(build,'ROOT',root)
    monkeypatch.setattr(build_workers,'STOP_GRACE_SECONDS',.15)
    pids=[]
    try:
        with pytest.raises(ValueError if failure=='parent' else subprocess.CalledProcessError):
            build.run_stages(job,('core','quest','r0','readable') if jobs==3 else ('core','r0','readable'),0,jobs)
        elapsed=time.monotonic()-failed_at[0]
        pids=[int((output/worker/(name+'.pid')).read_text()) for worker in worker_names for name in ('worker','verifier','leaf')]
        assert not any(running(pid) for pid in pids), 'cleanup returned with a live descendant'
        assert all(not (output/name/'late-output').exists() for name in worker_names)
        if ignore_term and os.name!='nt':assert elapsed>=.15  # TERM-resistant leaf required escalation.
        records=[json.loads(p.read_text()) for p in output.glob('*-worker-*.json')]
        assert len(records)==len(worker_names)
        assert all(r['worker_tree_shutdown_completed'] and not r['parent_completed'] for r in records)
        assert all(r['worker_returncode']!=0 for r in records)
    finally:
        # Also contain a regression of this test itself: kill only its fixture
        # PIDs. No child is allowed to linger when an assertion fails.
        for path in output.glob('*/*.pid'):
            pid=int(path.read_text())
            if running(pid):
                try:os.kill(pid,signal.SIGTERM if os.name=='nt' else signal.SIGKILL)
                except ProcessLookupError:pass


def test_successful_worker_keeps_exit_code_and_close_is_idempotent(tmp_path):
    script=tmp_path/'worker.py';marker=tmp_path/'body-ran'
    script.write_text('import sys\nfrom pathlib import Path\nPath(sys.argv[1]).touch()\n')
    tree=build_workers.WorkerTree([sys.executable,str(script),str(marker)],
        cwd=tmp_path,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    assert tree.process.wait(timeout=10)==0 and marker.exists()
    tree.stop();tree.stop()
    assert tree.closed and tree.process.returncode==0


def test_windows_assignment_failure_never_releases_build_body(tmp_path,monkeypatch):
    script=tmp_path/'worker.py';marker=tmp_path/'must-not-run'
    script.write_text('import sys\nfrom pathlib import Path\nPath(sys.argv[1]).touch()\n')
    jobs=[];processes=[];popen=subprocess.Popen
    class Job:
        def __init__(self):self.closed=False;jobs.append(self)
        def assign(self,pid):
            time.sleep(.1)
            assert not marker.exists(), 'build ran before Job assignment'
            raise OSError('synthetic Job assignment failure')
        def close(self):self.closed=True
    def observed(*a,**kw):
        p=popen(*a,**kw);processes.append(p);return p
    monkeypatch.setattr(build_workers,'WINDOWS',True)
    monkeypatch.setattr(build_workers,'_WindowsJob',Job)
    monkeypatch.setattr(build_workers.subprocess,'Popen',observed)
    with pytest.raises(OSError,match='Job assignment failure'):
        build_workers.WorkerTree([sys.executable,str(script),str(marker)],
            cwd=tmp_path,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    assert len(processes)==1 and processes[0].poll() is not None
    assert jobs[0].closed and not marker.exists()


def test_spawn_failure_closes_unused_windows_job(tmp_path,monkeypatch):
    jobs=[]
    class Job:
        def __init__(self):self.closed=False;jobs.append(self)
        def close(self):self.closed=True
    def failed(*a,**kw):raise OSError('synthetic spawn failure')
    monkeypatch.setattr(build_workers,'WINDOWS',True)
    monkeypatch.setattr(build_workers,'_WindowsJob',Job)
    monkeypatch.setattr(build_workers.subprocess,'Popen',failed)
    with pytest.raises(OSError,match='spawn failure'):
        build_workers.WorkerTree([sys.executable,'unused.py'],cwd=tmp_path)
    assert jobs[0].closed
