"""Fixed R0/Quest overlap; every dependent stage still requires qualification."""
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

import pytest

from scripts import build


def job(tmp_path,events):
    return SimpleNamespace(output=tmp_path,source=tmp_path/'source',history=None,
        core=lambda:events.append('core'),r0=lambda:events.append('r0'),
        readable=lambda:events.append('readable'),
        dependency_inputs=lambda name:events.append('qualified '+name))


def test_serial_path_keeps_stage_order_without_starting_worker(tmp_path,monkeypatch):
    events=[]; j=job(tmp_path,events)
    monkeypatch.setattr(build.subprocess,'Popen',lambda *a,**kw:pytest.fail('serial spawned a worker'))
    build.run_stages(j,('core','r0','readable'),0,1)
    assert events==['core','r0','readable']
    assert not list(tmp_path.glob('r0-worker-*'))


def test_real_worker_overlaps_main_and_joins_before_consumer(tmp_path,monkeypatch):
    root=tmp_path/'implementation'; (root/'scripts').mkdir(parents=True)
    (root/'scripts/build.py').write_text('''import argparse,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output-root');a,_=p.parse_known_args()
out=Path(a.output_root);(out/'worker-start').touch()
end=time.monotonic()+5
while not (out/'parent-start').exists():
 if time.monotonic()>end:raise SystemExit(3)
 time.sleep(.01)
(out/'worker-complete').touch()
''')
    events=[];j=job(tmp_path,events)
    def core():
        (tmp_path/'parent-start').touch()
        end=time.monotonic()+5
        while not (tmp_path/'worker-start').exists():
            if time.monotonic()>end:pytest.fail('worker not concurrent')
            time.sleep(.01)
        events.append('core')
    def qualify(name):
        assert (tmp_path/'worker-complete').exists()
        events.append('qualified '+name)
    j.core=core;j.dependency_inputs=qualify
    monkeypatch.setattr(build,'ROOT',root)
    build.run_stages(j,('core','r0','readable'),0,2)
    assert events==['core','qualified r0','readable']
    receipt=json.loads(next(tmp_path.glob('r0-worker-*.json')).read_text())
    assert receipt['worker_returncode']==0 and receipt['parent_completed']
    assert Path(receipt['stdout']).exists() and Path(receipt['stderr']).exists()


class Worker:
    def __init__(self,args,**kwargs):
        self.args=args;self.pid=123;self.returncode=None;self.terminated=False;self.killed=False
    def poll(self):return self.returncode
    def wait(self,timeout=None):
        if self.returncode is None:self.returncode=0
        return self.returncode
    def terminate(self):self.terminated=True;self.returncode=-15
    def kill(self):self.killed=True;self.returncode=-9


class Tree:
    def __init__(self,worker):self.process=worker
    def stop(self):
        if self.process.poll() is None:self.process.terminate()
        self.process.wait()


def fake_tree(*args,**kwargs):return Tree(Worker(*args,**kwargs))


def test_zero_exit_without_qualified_artifact_does_not_reach_consumer(tmp_path,monkeypatch):
    events=[];j=job(tmp_path,events)
    monkeypatch.setattr(build,'WorkerTree',fake_tree)
    def rejected(_):raise ValueError('no qualified artifact')
    j.dependency_inputs=rejected
    with pytest.raises(ValueError,match='no qualified artifact'):
        build.run_stages(j,('core','r0','readable'),0,2)
    assert events==['core']
    assert not json.loads(next(tmp_path.glob('r0-worker-*.json')).read_text())['parent_completed']


@pytest.mark.parametrize('failure', ['parent','worker'])
def test_failure_stops_before_consumers_and_retains_logs(tmp_path,monkeypatch,failure):
    events=[];j=job(tmp_path,events);workers=[]
    def spawn(*args,**kwargs):
        w=Worker(*args,**kwargs);workers.append(w);return Tree(w)
    monkeypatch.setattr(build,'WorkerTree',spawn)
    def core():
        if failure=='parent':raise RuntimeError('parent failed')
        workers[0].returncode=7
    j.core=core
    with pytest.raises(RuntimeError if failure=='parent' else subprocess.CalledProcessError):
        build.run_stages(j,('core','r0','readable'),0,2)
    assert not events
    assert workers[0].terminated == (failure=='parent')
    receipt=json.loads(next(tmp_path.glob('r0-worker-*.json')).read_text())
    assert not receipt['parent_completed']
    assert receipt['worker_returncode']!=0
    assert Path(receipt['stdout']).exists() and Path(receipt['stderr']).exists()


def test_three_jobs_qualifies_every_quest_output_before_continuing(tmp_path,monkeypatch):
    events=[];j=job(tmp_path,events);workers=[]
    def spawn(*args,**kwargs):
        w=Worker(*args,**kwargs);workers.append(w);return Tree(w)
    monkeypatch.setattr(build,'WorkerTree',spawn)
    j.dependency_inputs=lambda *names:events.extend('qualified '+n for n in names)
    j.quest=lambda:pytest.fail('parent duplicated Quest worker')
    build.run_stages(j,('core','quest','r0','readable'),0,3)
    assert len(workers)==2
    assert events==['core','qualified quest_talk','qualified quest_narrative',
                    'qualified native_message','qualified quest_review',
                    'qualified quest_review_binding','qualified talk_physical',
                    'qualified r0','readable']
    assert all(w.args[w.args.index('--jobs')+1]=='1' for w in workers)
    assert len(list(tmp_path.glob('*-worker-*.json')))==2


def test_second_spawn_failure_cancels_the_already_started_worker(tmp_path,monkeypatch):
    events=[];j=job(tmp_path,events);workers=[]
    def spawn(*args,**kwargs):
        if workers:raise OSError('second worker could not start')
        w=Worker(*args,**kwargs);workers.append(w);return Tree(w)
    monkeypatch.setattr(build,'WorkerTree',spawn)
    with pytest.raises(OSError,match='could not start'):
        build.run_stages(j,('core','quest','r0','readable'),0,3)
    assert not events
    assert workers[0].terminated
    records=[json.loads(p.read_text()) for p in tmp_path.glob('*-worker-*.json')]
    assert len(records)==2
    assert all(not r['parent_completed'] for r in records)
    assert any(r['worker_returncode'] is None for r in records)
    assert any(r['worker_tree_shutdown_completed'] is None for r in records)


def test_cleanup_error_does_not_leave_the_other_worker_running(tmp_path,monkeypatch):
    events=[];j=job(tmp_path,events);workers=[]
    def spawn(*args,**kwargs):
        w=Worker(*args,**kwargs);workers.append(w)
        if len(workers)==1:
            return SimpleNamespace(process=w,stop=lambda:(_ for _ in ()).throw(OSError('stop failed')))
        return Tree(w)
    monkeypatch.setattr(build,'WorkerTree',spawn)
    def fail():raise ValueError('parent stopped')
    j.core=fail
    with pytest.raises(RuntimeError,match='worker tree cleanup failed'):
        build.run_stages(j,('core','quest','r0','readable'),0,3)
    assert workers[1].terminated
    records=[json.loads(p.read_text()) for p in tmp_path.glob('*-worker-*.json')]
    assert len(records)==2 and all(not r['parent_completed'] for r in records)
    assert sorted(r['worker_tree_shutdown_completed'] for r in records)==[False,True]
    assert not events


@pytest.mark.parametrize('jobs',[0,4,-1,True])
def test_invalid_worker_budget_rejected_before_starting_anything(tmp_path,monkeypatch,jobs):
    events=[];j=job(tmp_path,events)
    monkeypatch.setattr(build.subprocess,'Popen',lambda *a,**kw:pytest.fail('invalid jobs started a worker'))
    with pytest.raises(ValueError,match='jobs'):
        build.run_stages(j,('core','r0','readable'),0,jobs)
    assert not events
    assert not list(tmp_path.glob('*-worker-*'))


@pytest.mark.parametrize('jobs', [2, 3])
def test_ready_codex_uses_freed_slot_and_is_qualified_before_consumer(tmp_path, monkeypatch, jobs):
    events=[]; j=job(tmp_path,events); workers=[]
    def spawn(*args, **kwargs):
        stage=args[0][args[0].index('--stage')+1]
        if stage=='codex':
            assert 'core' in events
        w=Worker(*args, **kwargs); workers.append(w); return Tree(w)
    monkeypatch.setattr(build,'WorkerTree',spawn)
    def core():
        events.append('core')
        workers[0].returncode=0  # R0 has finished, freeing one slot.
    j.core=core
    j.character=lambda:events.append('character')
    j.codex=lambda:pytest.fail('duplicate Codex execution')
    j.dependency_inputs=lambda *names:events.extend('qualified '+n for n in names)
    build.run_stages(j,('core','character','codex','r0','readable'),0,jobs)
    assert events==['core','character','qualified codexquest','qualified r0','readable']
    assert len(workers)==2
    assert json.loads(next(tmp_path.glob('codex-worker-*.json')).read_text())['parent_completed']


def test_codex_stays_serial_when_worker_slot_is_busy(tmp_path,monkeypatch):
    events=[]; j=job(tmp_path,events)
    monkeypatch.setattr(build,'WorkerTree',fake_tree)
    j.codex=lambda:events.append('codex')
    build.run_stages(j,('core','codex','r0','readable'),0,2)
    assert events==['core','codex','qualified r0','readable']
    assert not list(tmp_path.glob('codex-worker-*'))


def test_codex_failed_qualification_stops_before_index(tmp_path,monkeypatch):
    events=[]; j=job(tmp_path,events); workers=[]
    def spawn(*args,**kwargs):
        w=Worker(*args,**kwargs); workers.append(w); return Tree(w)
    monkeypatch.setattr(build,'WorkerTree',spawn)
    j.core=lambda:setattr(workers[0],'returncode',0)
    def qualify(name):
        if name=='codexquest':raise ValueError('Codex receipt drift')
    j.dependency_inputs=qualify
    j.index=lambda:pytest.fail('unqualified Codex reached index')
    with pytest.raises(ValueError,match='Codex receipt drift'):
        build.run_stages(j,('core','codex','r0','index'),0,2)
    assert not json.loads(next(tmp_path.glob('codex-worker-*.json')).read_text())['parent_completed']
