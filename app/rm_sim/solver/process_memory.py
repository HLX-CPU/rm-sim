"""Windows process memory readings; unavailable readings are explicit None."""
import ctypes
from ctypes import wintypes
import os
import sys


class _Counters(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in (
            'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
            'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
            'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]


def process_memory(pid=None):
    if sys.platform != 'win32':
        return None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x0400 | 0x0010, False, int(pid or os.getpid()))
    if not handle:
        return None
    try:
        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return {'pid': int(pid or os.getpid()), 'working_set_bytes': int(counters.WorkingSetSize),
                'peak_working_set_bytes': int(counters.PeakWorkingSetSize),
                'private_bytes': int(counters.PrivateUsage),
                'peak_commit_bytes': int(counters.PeakPagefileUsage)}
    finally:
        kernel.CloseHandle(handle)
