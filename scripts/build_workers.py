"""Own the lifetime of one build worker and its verifier descendants.

POSIX workers have a new session/process group. Windows workers enter a Job
Object before running any build code; an stdin gate closes the assignment
race. These are build subprocesses, not a general task scheduler.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

WINDOWS = os.name == 'nt'
STOP_GRACE_SECONDS = 30
KILL_WAIT_SECONDS = 5
BOOTSTRAP = '''import runpy,sys
if sys.stdin.buffer.read(1) != b'G':
    raise SystemExit('worker startup not authorized')
# Keep the valid read handle for nested subprocesses (also on Windows).
# The coordinator closes its writer after release, so subsequent reads get EOF.
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
'''


class _Accounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in
                ('user', 'kernel', 'period_user', 'period_kernel')] + [
        (name, ctypes.c_uint32) for name in
        ('page_faults', 'total', 'active', 'terminated')]


class _BasicLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                ('flags', ctypes.c_uint32), ('min_working_set', ctypes.c_size_t),
                ('max_working_set', ctypes.c_size_t), ('process_limit', ctypes.c_uint32),
                ('affinity', ctypes.c_size_t), ('priority', ctypes.c_uint32),
                ('scheduling', ctypes.c_uint32)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', _BasicLimits), ('io', ctypes.c_uint64 * 6),
                ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]


class _WindowsJob:
    """Documented Win32 Job API; no Popen private handles or taskkill races.

    https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
    """

    def __init__(self):
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        handle, uint, pointer = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p
        signatures = {
            'CreateJobObjectW': (handle, [pointer, ctypes.c_wchar_p]),
            'SetInformationJobObject': (ctypes.c_int, [handle, ctypes.c_int, pointer, uint]),
            'OpenProcess': (handle, [uint, ctypes.c_int, uint]),
            'AssignProcessToJobObject': (ctypes.c_int, [handle, handle]),
            'TerminateJobObject': (ctypes.c_int, [handle, uint]),
            'QueryInformationJobObject': (ctypes.c_int, [handle, ctypes.c_int, pointer, uint, pointer]),
            'IsProcessInJob': (ctypes.c_int, [handle, handle, pointer]),
            'WaitForSingleObject': (uint, [handle, uint]),
            'CloseHandle': (ctypes.c_int, [handle]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(self.api, name)
            function.restype, function.argtypes = result, arguments
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = _ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def assign(self, pid):
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE; use the public PID, not _handle.
        process = self.api.OpenProcess(0x0100 | 0x0001, False, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.api.CloseHandle(process)

    def process_handles(self):
        # Hold original process objects before requesting termination. Job
        # accounting can reach zero before their wait handles are signaled.
        capacity = 16
        while True:
            class ProcessList(ctypes.Structure):
                _fields_ = [('assigned', ctypes.c_uint32), ('count', ctypes.c_uint32),
                            ('pids', ctypes.c_size_t * capacity)]
            members = ProcessList()
            if self.api.QueryInformationJobObject(self.handle, 3, ctypes.byref(members), ctypes.sizeof(members), None):
                break
            error = ctypes.get_last_error()
            if error != 234:  # ERROR_MORE_DATA
                raise ctypes.WinError(error)
            capacity = max(capacity * 2, members.assigned)
        handles = []
        try:
            for pid in members.pids[:members.count]:
                process = self.api.OpenProcess(0x00100000 | 0x1000, False, pid)
                if not process:
                    error = ctypes.get_last_error()
                    if error == 87:  # Exited and gone between enumeration/open.
                        continue
                    raise ctypes.WinError(error)
                handles.append(process)
                belongs = ctypes.c_int()
                if not self.api.IsProcessInJob(process, self.handle, ctypes.byref(belongs)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not belongs.value:  # Recycled PID, never wait/kill another job.
                    self.api.CloseHandle(handles.pop())
            return handles
        except BaseException:
            for process in handles:
                self.api.CloseHandle(process)
            raise

    def stop(self):
        deadline = time.monotonic() + KILL_WAIT_SECONDS
        handles = self.process_handles()
        try:
            self._stop_and_wait(deadline, handles)
        finally:
            for process in handles:
                self.api.CloseHandle(process)

    def _stop_and_wait(self, deadline, handles):
        if not self.api.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        while True:
            accounting = _Accounting()
            if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if accounting.active == 0:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError('Windows build worker job did not stop')
            time.sleep(.01)
        for process in handles:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            result = self.api.WaitForSingleObject(process, remaining_ms)
            if result == 258:
                raise TimeoutError('Windows build descendant did not finish termination')
            if result != 0:
                raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            if not self.api.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None


def _group_running(pgid):
    if sys.platform.startswith('linux'):
        # Orphans may remain zombies until init reaps them. They have exited
        # and cannot write; wait for every live group member, not just its root.
        for entry in Path('/proc').iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
                if int(fields[2]) == pgid and fields[0] not in ('Z', 'X'):
                    return True
            except (FileNotFoundError, ProcessLookupError):
                continue
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def _signal_group(pgid, sig):
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def _wait_group(pgid, seconds):
    deadline = time.monotonic() + seconds
    while _group_running(pgid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(.01)
    return True


class WorkerTree:
    """Start an isolated worker, then stop/wait its entire owned process tree."""

    def __init__(self, command, **options):
        self.process = None
        self.job = _WindowsJob() if WINDOWS else None
        self.closed = False
        managed = self.job is None
        try:
            self.process = subprocess.Popen(
                [command[0], '-c', BOOTSTRAP, *command[1:]], stdin=subprocess.PIPE,
                start_new_session=not WINDOWS, **options)
            if self.job is not None:
                self.job.assign(self.process.pid)
                managed = True
            self.process.stdin.write(b'G')
            self.process.stdin.flush()
            self.process.stdin.close()
        except BaseException:
            # Before assignment the gate prevents descendants; after release
            # even a pipe error must shut down the entire managed tree.
            try:
                if self.process is not None:
                    try:
                        self.process.stdin.close()
                    except OSError:
                        pass  # A dead bootstrap may already have broken the pipe.
                    if managed:
                        self.stop()
                    else:
                        if self.process.poll() is None:
                            self.process.terminate()
                        self.process.wait(timeout=KILL_WAIT_SECONDS)
            finally:
                if self.job is not None and not self.closed:
                    self.job.close()
            raise

    def stop(self):
        if self.closed:
            return
        try:
            if self.job is not None:
                self.job.stop()
            else:
                _signal_group(self.process.pid, signal.SIGTERM)
                if not _wait_group(self.process.pid, STOP_GRACE_SECONDS):
                    _signal_group(self.process.pid, signal.SIGKILL)
                    if not _wait_group(self.process.pid, KILL_WAIT_SECONDS):
                        raise TimeoutError('POSIX build worker group did not stop')
            self.process.wait(timeout=KILL_WAIT_SECONDS)
        finally:
            if self.job is not None:
                self.job.close()
            self.closed = True
