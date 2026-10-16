"""Windows process helpers, stdlib only (ctypes). Imported only when os.name == "nt".

Windows has no POSIX process groups. The command is put in a Job Object right after it starts; every process it
spawns afterwards joins the job, so "is anything of this command still alive" and "kill all of it" are exact even
when intermediate parents have exited. Two things are weaker than killpg: a process the command spawns in the few
milliseconds before it is assigned to the job is not in it (it is still found by the fallback below), and if the job
cannot be created or assigned (some restricted environments) hca falls back to walking ParentProcessId over a
Toolhelp snapshot, which loses grandchildren whose parent has already exited and relies on creation times to
avoid reused pids.
"""

from __future__ import annotations

import ctypes
import subprocess
from ctypes import wintypes

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_k32.CloseHandle.argtypes = (wintypes.HANDLE,)
_k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
_k32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
_k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_k32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)

_k32.Process32FirstW.argtypes = _k32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.c_void_p)

_k32.CreateJobObjectW.restype = wintypes.HANDLE
_k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
_k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
_k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
_k32.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.c_void_p)
_k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)

_QUERY_LIMITED = 0x1000
_STILL_ACTIVE = 259
_TH32CS_SNAPPROCESS = 0x2
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


_PROCESS_SET_QUOTA, _PROCESS_TERMINATE = 0x0100, 0x0001
_JOB_KILL_ON_CLOSE = 0x2000
_JOB_EXTENDED_LIMITS, _JOB_BASIC_ACCOUNTING = 9, 1
KILLED_EXIT_CODE = 1                      # what `taskkill /F` and TerminateJobObject give the victims


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("a", "b", "c", "d", "e", "f")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]


def start_time(pid):
    """Creation time of `pid` as a FILETIME integer (100 ns ticks), or None if it cannot be opened."""
    handle = _k32.OpenProcess(_QUERY_LIMITED, False, pid)
    if not handle:
        return None
    try:
        created, ignored1, ignored2, ignored3 = (wintypes.FILETIME() for _ in range(4))
        if not _k32.GetProcessTimes(handle, created, ignored1, ignored2, ignored3):
            return None
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        _k32.CloseHandle(handle)


def is_running(pid) -> bool:
    """True while the process has not exited. An exited process whose object is held open by a handle reads as dead."""
    handle = _k32.OpenProcess(_QUERY_LIMITED, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(_k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == _STILL_ACTIVE
    finally:
        _k32.CloseHandle(handle)


def _edges() -> list:
    """[(pid, parent pid)] for every process in a snapshot."""
    snap = _k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == _INVALID_HANDLE:
        return []
    found = []
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        ok = _k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            found.append((entry.th32ProcessID, entry.th32ParentProcessID))
            ok = _k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _k32.CloseHandle(snap)
    return found


def descendants(pid, since=None) -> list:
    """Running descendants of `pid` (children, grandchildren, ...), excluding `pid` itself.

    `since` is the command's own start_time: a process created before it cannot be its child, so a reused pid is skipped.
    """
    edges = _edges()
    seen, queue, found = {pid}, [pid], []
    while queue:
        parent = queue.pop()
        for child, ppid in edges:
            if ppid != parent or child in seen:
                continue
            seen.add(child)
            queue.append(child)                   # walk through exited parents: their children are still linked
            if not is_running(child):
                continue
            created = start_time(child)
            if since is not None and created is not None and created < since:
                continue
            found.append(child)
    return found


def kill_tree(pid, since=None) -> bool:
    """`taskkill /T /F` on the command and on every descendant found first (so orphans of a dead leader die too)."""
    victims = ([pid] if is_running(pid) else []) + descendants(pid, since)
    if not victims:
        return False
    argv = ["taskkill", "/T", "/F"]
    for victim in victims:
        argv += ["/PID", str(victim)]
    try:
        subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return True


def tree_alive(pid, since=None) -> bool:
    return is_running(pid) or bool(descendants(pid, since))


class Tree:
    """One started command and everything it spawned: a Job Object when possible, parent-pid walking otherwise."""

    def __init__(self, pid):
        self.pid = pid
        self.since = start_time(pid)
        self.job = self._job_for(pid)

    @staticmethod
    def _job_for(pid):
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            return None
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_KILL_ON_CLOSE      # nothing outlives a crashed hca
        proc = _k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        ok = bool(proc) and _k32.SetInformationJobObject(job, _JOB_EXTENDED_LIMITS, ctypes.byref(limits),
                                                         ctypes.sizeof(limits))             and _k32.AssignProcessToJobObject(job, proc)
        if proc:
            _k32.CloseHandle(proc)
        if not ok:
            _k32.CloseHandle(job)
            return None
        return job

    def alive(self) -> bool:
        if self.job:
            info = _Accounting()
            if _k32.QueryInformationJobObject(self.job, _JOB_BASIC_ACCOUNTING, ctypes.byref(info),
                                              ctypes.sizeof(info), None):
                return info.ActiveProcesses > 0
        return tree_alive(self.pid, self.since)

    def kill(self) -> bool:
        """TerminateJobObject, then `taskkill /T /F` as well (it also covers a failed or late job assignment)."""
        hit = bool(self.job) and bool(_k32.TerminateJobObject(self.job, KILLED_EXIT_CODE))
        return kill_tree(self.pid, self.since) or hit

    def close(self) -> None:
        if self.job:
            _k32.CloseHandle(self.job)          # KILL_ON_JOB_CLOSE reaps anything still inside
            self.job = None
