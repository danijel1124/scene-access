"""Runs plain pytest and kills it at once if it or any process it started ever shows a window on the
user's desktop. Usage: python tools\\guarded_pytest.py <log file> <pytest args...>"""

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import win32gui
import win32process

TH32CS_SNAPPROCESS = 0x00000002


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def started(pid: int) -> int | None:
    """When the process started, in Windows file time, or None if it cannot be asked."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        created, ended, kernel, user = (ctypes.c_ulonglong() for _ in range(4))
        ok = kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(ended), ctypes.byref(kernel),
                                      ctypes.byref(user))
        return created.value if ok else None
    finally:
        kernel32.CloseHandle(handle)


def descendants(root: int) -> set[int]:
    """root and every process it started, directly or not. A process only counts as started by another if it
    started later: Windows reuses process IDs, so the user's Firefox, whose parent ended yesterday, once looked
    like a child of pytest because a test process got that parent's old ID (18.11.2026)."""
    parents: dict[int, int] = {}
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    found, changed = {root}, True
    times: dict[int, int | None] = {}

    def start_of(pid: int) -> int | None:
        if pid not in times:
            times[pid] = started(pid)
        return times[pid]

    root_start = start_of(root)
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in found and pid not in found:
                child, father = start_of(pid), start_of(parent)
                # Left out only when known to be older than its parent or than pytest; a process whose start
                # cannot be read counts, as a needless stop of the tests is better than a window missed.
                if child is not None and ((father is not None and child < father)
                                          or (root_start is not None and child < root_start)):
                    continue
                found.add(pid)
                changed = True
    return found


log_path, args = sys.argv[1], sys.argv[2:]
with open(log_path, "w", encoding="utf-8") as log:
    tests = subprocess.Popen([sys.executable, "-m", "pytest", *args], stdout=log, stderr=subprocess.STDOUT,
                             cwd=Path(__file__).resolve().parent.parent)
    seen: list[str] = []

    while tests.poll() is None:
        watched = descendants(tests.pid)

        def check(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1] in watched:
                seen.append(f"{win32gui.GetWindowText(hwnd) or '(ohne Titel)'} "
                            f"(PID {win32process.GetWindowThreadProcessId(hwnd)[1]})")

        win32gui.EnumWindows(check, None)
        if seen:
            # Only the processes checked to be pytest's own: taskkill /T follows the same reused IDs and could
            # end a program of the user's.
            # Asked again: a process may have started since the windows were checked.
            for pid in sorted(descendants(tests.pid), key=lambda pid: pid != tests.pid):
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            break
        time.sleep(0.2)
    tests.wait()
print("Sichtbare Fenster auf dem Nutzer-Desktop:", seen or "keine")
print("pytest-Exit:", tests.returncode)
