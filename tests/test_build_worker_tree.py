"""Real worker -> verifier -> writer lifetime, on Linux and native Windows."""
import ctypes
import errno
import json
import os
from pathlib import Path
import signal
import select
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from scripts import build, build_workers


class ObservedProcess:
    """Hold identity BEFORE cleanup: a later OpenProcess(PID) may find reuse."""
    def __init__(self,pid):
        self.pid=pid;self.fd=None
        if os.name=='nt':
            self.api=ctypes.WinDLL('kernel32',use_last_error=True)
            signatures={
                'OpenProcess':(ctypes.c_void_p,[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]),
                'WaitForSingleObject':(ctypes.c_uint32,[ctypes.c_void_p,ctypes.c_uint32]),
                'GetExitCodeProcess':(ctypes.c_int,[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]),
                'TerminateProcess':(ctypes.c_int,[ctypes.c_void_p,ctypes.c_uint32]),
                'CloseHandle':(ctypes.c_int,[ctypes.c_void_p]),
            }
            for name,(result,args) in signatures.items():
                fn=getattr(self.api,name);fn.restype=result;fn.argtypes=args
            self.handle=self.api.OpenProcess(0x00100000|0x1000|0x0001,False,pid)
            if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        else:
            self.birth=self.stat()[19]
            if hasattr(os,'pidfd_open'):
                try:self.fd=os.pidfd_open(pid)
                except OSError as exc:
                    if exc.errno not in (errno.ENOSYS,errno.EPERM,errno.EACCES):raise

    def stat(self):
        return (Path('/proc')/str(self.pid)/'stat').read_text().rsplit(')',1)[1].split()

    def snapshot(self):
        if os.name=='nt':
            status=self.api.WaitForSingleObject(self.handle,0)
            if status not in (0,258):raise ctypes.WinError(ctypes.get_last_error())
            code=ctypes.c_uint32()
            if not self.api.GetExitCodeProcess(self.handle,ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            return {'pid':self.pid,'running':status==258,'wait_status':status,'exit_code':code.value}
        if self.fd is not None:
            poll=select.poll();poll.register(self.fd,select.POLLIN)
            return {'pid':self.pid,'running':not bool(poll.poll(0))}
        try:
            fields=self.stat()
            return {'pid':self.pid,'running':fields[19]==self.birth and fields[0] not in ('Z','X')}
        except FileNotFoundError:return {'pid':self.pid,'running':False}

    def close(self):
        if os.name=='nt':self.api.CloseHandle(self.handle)
        elif self.fd is not None:os.close(self.fd)

    def stop_fixture(self):
        if not self.snapshot()['running']:return
        if os.name=='nt':
            if not self.api.TerminateProcess(self.handle,1):
                if self.snapshot()['running']:raise ctypes.WinError(ctypes.get_last_error())
            assert self.api.WaitForSingleObject(self.handle,5000)==0
        else:
            try:
                if self.fd is not None:signal.pidfd_send_signal(self.fd,signal.SIGKILL)
                elif self.stat()[19]==self.birth:os.kill(self.pid,signal.SIGKILL)
            except ProcessLookupError:pass


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
 end=time.monotonic()+10
 while not (out/'allow-worker-exit').exists():
  if time.monotonic()>end:os._exit(8)
  time.sleep(.01)
 os._exit(7)
child.wait()
'''
    (root/'scripts/build.py').write_text('VERIFIER='+repr(verifier)+'\nLEAF='+repr(leaf)+
        '\nIGNORE='+repr(str(ignore_term))+'\nFAILURE='+repr(failure)+'\n'+script)
    failed_at=[]
    probes={}
    worker_names=('r0',) if jobs==2 else ('r0','quest')
    def core():
        for name in worker_names:wait_for(output/name/'ready')
        for worker in worker_names:
            for name in ('worker','verifier','leaf'):
                pid=int((output/worker/(name+'.pid')).read_text())
                probes[worker+'/'+name]=ObservedProcess(pid)
        assert all(p.snapshot()['running'] for p in probes.values())
        failed_at.append(time.monotonic())
        if failure=='parent':raise ValueError('synthetic parent failure')
        for name in worker_names:(output/name/'allow-worker-exit').touch()
    def unexpected(*args):pytest.fail('continued to a consumer after failure')
    job=SimpleNamespace(output=output,source=tmp_path/'source',history=None,
        core=core,r0=unexpected,readable=unexpected,dependency_inputs=unexpected)
    monkeypatch.setattr(build,'ROOT',root)
    monkeypatch.setattr(build_workers,'STOP_GRACE_SECONDS',.15)
    try:
        with pytest.raises(ValueError if failure=='parent' else subprocess.CalledProcessError) as caught:
            build.run_stages(job,('core','quest','r0','readable') if jobs==3 else ('core','r0','readable'),0,jobs)
        if failure=='worker':assert caught.value.returncode==7
        elapsed=time.monotonic()-failed_at[0]
        states={name:p.snapshot() for name,p in probes.items()}
        assert not any(s['running'] for s in states.values()), json.dumps(states)
        assert all(not (output/name/'late-output').exists() for name in worker_names)
        if ignore_term and os.name!='nt':assert elapsed>=.15  # TERM-resistant leaf required escalation.
        records=[json.loads(p.read_text()) for p in output.glob('*-worker-*.json')]
        assert len(records)==len(worker_names)
        assert all(r['worker_tree_shutdown_completed'] and not r['parent_completed'] for r in records)
        assert all(r['worker_returncode']!=0 for r in records)
    finally:
        # Also contain a regression of this test itself: kill only its fixture
        # Stable original identities only, never a PID recycled by the runner.
        for probe in probes.values():
            try:probe.stop_fixture()
            finally:probe.close()


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


@pytest.mark.parametrize('wait_result', [0,258])
def test_windows_job_waits_for_original_handles_after_accounting_zero(wait_result):
    events=[]
    class API:
        def TerminateJobObject(self,handle,code):events.append('terminate');return 1
        def QueryInformationJobObject(self,handle,kind,buffer,size,unused):
            assert kind==1
            ctypes.cast(buffer,ctypes.POINTER(build_workers._Accounting)).contents.active=0
            events.append('accounting-zero');return 1
        def WaitForSingleObject(self,handle,timeout):
            assert handle==123 and 0<=timeout<=5000
            events.append('wait-original');return wait_result
        def CloseHandle(self,handle):events.append(('close',handle));return 1
    job=object.__new__(build_workers._WindowsJob);job.api=API();job.handle=456
    def original_handles():events.append('retain-original');return [123]
    job.process_handles=original_handles
    if wait_result==258:
        with pytest.raises(TimeoutError,match='descendant did not finish'):job.stop()
    else:job.stop()
    assert events==['retain-original','terminate','accounting-zero','wait-original',('close',123)]


def test_windows_job_snapshot_excludes_recycled_pid():
    closed=[]
    class API:
        def QueryInformationJobObject(self,handle,kind,buffer,size,unused):
            assert kind==3
            words=ctypes.cast(buffer,ctypes.POINTER(ctypes.c_uint32));words[0]=2;words[1]=2
            pids=ctypes.cast(ctypes.addressof(buffer._obj)+8,ctypes.POINTER(ctypes.c_size_t))
            pids[0]=21;pids[1]=22
            return 1
        def OpenProcess(self,rights,inherit,pid):return pid+100
        def IsProcessInJob(self,process,handle,belongs):
            ctypes.cast(belongs,ctypes.POINTER(ctypes.c_int)).contents.value=int(process==121)
            return 1
        def CloseHandle(self,handle):closed.append(handle);return 1
    job=object.__new__(build_workers._WindowsJob);job.api=API();job.handle=456
    assert job.process_handles()==[121]
    assert closed==[122]
