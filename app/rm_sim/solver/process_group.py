"""Own a solver process tree; Windows children start only after job assignment.

Job accounting is committed private memory, not working set. No processes outside
this one job are controlled. See Microsoft Job Objects / CREATE_SUSPENDED docs.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes as w
import os
import signal
import subprocess


class _BasicLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                ('flags', w.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                ('active_limit', w.DWORD), ('affinity', ctypes.c_size_t),
                ('priority', w.DWORD), ('scheduling', w.DWORD)]


class _Limits(ctypes.Structure):
    _fields_ = [('basic', _BasicLimits), ('io', ctypes.c_ulonglong*6),
                ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('thread', w.DWORD),
                ('owner', w.DWORD), ('base', w.LONG), ('delta', w.LONG), ('flags', w.DWORD)]


def _windows_api():
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateJobObjectW': ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
        'SetInformationJobObject': ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
        'QueryInformationJobObject': ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p], w.BOOL),
        'AssignProcessToJobObject': ([w.HANDLE, w.HANDLE], w.BOOL),
        'TerminateJobObject': ([w.HANDLE, w.UINT], w.BOOL),
        'CloseHandle': ([w.HANDLE], w.BOOL),
        'CreateToolhelp32Snapshot': ([w.DWORD, w.DWORD], w.HANDLE),
        'Thread32First': ([w.HANDLE, ctypes.POINTER(_ThreadEntry)], w.BOOL),
        'Thread32Next': ([w.HANDLE, ctypes.POINTER(_ThreadEntry)], w.BOOL),
        'OpenThread': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        'ResumeThread': ([w.HANDLE], w.DWORD),
    }
    for name, (args, result) in signatures.items():
        function = getattr(api,name)
        function.argtypes, function.restype = args, result
    return api


class OwnedProcessGroup:
    def __init__(self, command, *, cwd, memory_limit_bytes=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE):
        self._job = None
        self._api = None
        self.process = None
        self.memory_limit_bytes = int(memory_limit_bytes) if memory_limit_bytes is not None else None
        # Leave native-allocation/accounting headroom inside the public budget.
        # Local tests measured job peaks slightly above the configured OS limit;
        # the precise cause is not established, so preserve the original readings.
        self.job_memory_limit_bytes = int(self.memory_limit_bytes*.9) if self.memory_limit_bytes is not None else None
        if self.memory_limit_bytes is not None and self.memory_limit_bytes <= 0:
            raise ValueError('Process-tree memory limit must be positive')
        try:
            if os.name == 'nt':
                self._api = _windows_api()
                self._job = self._api.CreateJobObjectW(None,None)
                if not self._job:
                    raise ctypes.WinError(ctypes.get_last_error())
                limits = _Limits()
                limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                if self.memory_limit_bytes is not None:
                    limits.basic.flags |= 0x200  # JOB_OBJECT_LIMIT_JOB_MEMORY
                    limits.job_memory = self.job_memory_limit_bytes
                if not self._api.SetInformationJobObject(self._job,9,ctypes.byref(limits),ctypes.sizeof(limits)):
                    raise ctypes.WinError(ctypes.get_last_error())
                self.process = subprocess.Popen(command,cwd=cwd,stdout=stdout,stderr=stderr,
                                                creationflags=0x4 | subprocess.CREATE_NO_WINDOW)
                # CPython's retained process handle, owned by this Popen instance.
                if not self._api.AssignProcessToJobObject(self._job,int(self.process._handle)):
                    raise ctypes.WinError(ctypes.get_last_error())
                self._resume_suspended_process()
            else:
                self.process = subprocess.Popen(command,cwd=cwd,stdout=stdout,stderr=stderr,
                                                start_new_session=True)
        except BaseException:
            self.close()
            if self.process is not None and self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=10)
            raise

    def _resume_suspended_process(self):
        snapshot = self._api.CreateToolhelp32Snapshot(0x4,0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        resumed = 0
        try:
            entry = _ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            found = self._api.Thread32First(snapshot,ctypes.byref(entry))
            while found:
                if entry.owner == self.process.pid:
                    thread = self._api.OpenThread(0x2,False,entry.thread)  # THREAD_SUSPEND_RESUME
                    if not thread:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if self._api.ResumeThread(thread) == 0xffffffff:
                            raise ctypes.WinError(ctypes.get_last_error())
                        resumed += 1
                    finally:
                        self._api.CloseHandle(thread)
                found = self._api.Thread32Next(snapshot,ctypes.byref(entry))
        finally:
            self._api.CloseHandle(snapshot)
        if not resumed:
            raise RuntimeError('Suspended solver thread not found; solver was not started')

    def accounting(self):
        if not self._job:
            return {'peak_job_memory_bytes':None,'memory_limit_bytes':None,'method':'unavailable'}
        limits = _Limits()
        if not self._api.QueryInformationJobObject(self._job,9,ctypes.byref(limits),ctypes.sizeof(limits),None):
            raise ctypes.WinError(ctypes.get_last_error())
        return {'peak_job_memory_bytes':int(limits.peak_job),'memory_limit_bytes':self.memory_limit_bytes,
                'job_memory_limit_bytes':self.job_memory_limit_bytes,
                'method':'Windows Job Object: peak committed private memory of this process tree; not working set'}

    def terminate(self):
        if self._job:
            if not self._api.TerminateJobObject(self._job,1):
                raise ctypes.WinError(ctypes.get_last_error())
        elif self.process is not None and os.name != 'nt':
            try:
                os.killpg(self.process.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif self.process is not None and self.process.poll() is None:
            self.process.kill()

    def close(self):
        if self._job:
            self._api.CloseHandle(self._job)
            self._job = None

