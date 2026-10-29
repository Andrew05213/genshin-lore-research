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


def test_zero_exit_without_qualified_artifact_does_not_reach_consumer(tmp_path,monkeypatch):
    events=[];j=job(tmp_path,events)
    monkeypatch.setattr(build.subprocess,'Popen',Worker)
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
        w=Worker(*args,**kwargs);workers.append(w);return w
    monkeypatch.setattr(build.subprocess,'Popen',spawn)
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
        w=Worker(*args,**kwargs);workers.append(w);return w
    monkeypatch.setattr(build.subprocess,'Popen',spawn)
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
        w=Worker(*args,**kwargs);workers.append(w);return w
    monkeypatch.setattr(build.subprocess,'Popen',spawn)
    with pytest.raises(OSError,match='could not start'):
        build.run_stages(j,('core','quest','r0','readable'),0,3)
    assert not events
    assert workers[0].terminated
    records=[json.loads(p.read_text()) for p in tmp_path.glob('*-worker-*.json')]
    assert len(records)==2
    assert all(not r['parent_completed'] for r in records)
    assert any(r['worker_returncode'] is None for r in records)


def test_unresponsive_worker_is_killed_only_after_termination_timeout(tmp_path,monkeypatch):
    events=[];j=job(tmp_path,events);workers=[]
    class Unresponsive(Worker):
        def terminate(self):self.terminated=True
        def wait(self,timeout=None):
            if timeout is not None:raise subprocess.TimeoutExpired(self.args,timeout)
            return self.returncode
    def spawn(*args,**kwargs):
        w=Unresponsive(*args,**kwargs);workers.append(w);return w
    monkeypatch.setattr(build.subprocess,'Popen',spawn)
    def fail():raise ValueError('parent stopped')
    j.core=fail
    with pytest.raises(ValueError,match='parent stopped'):
        build.run_stages(j,('core','r0','readable'),0,2)
    assert workers[0].terminated and workers[0].killed
    assert not events


@pytest.mark.parametrize('jobs',[0,4,-1,True])
def test_invalid_worker_budget_rejected_before_starting_anything(tmp_path,monkeypatch,jobs):
    events=[];j=job(tmp_path,events)
    monkeypatch.setattr(build.subprocess,'Popen',lambda *a,**kw:pytest.fail('invalid jobs started a worker'))
    with pytest.raises(ValueError,match='jobs'):
        build.run_stages(j,('core','r0','readable'),0,jobs)
    assert not events
    assert not list(tmp_path.glob('*-worker-*'))
