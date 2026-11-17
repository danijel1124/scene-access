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


def descendants(root: int) -> set[int]:
    """root and every process it started, directly or not."""
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
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in found and pid not in found:
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
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(tests.pid)], capture_output=True)
            break
        time.sleep(0.2)
    tests.wait()
print("Sichtbare Fenster auf dem Nutzer-Desktop:", seen or "keine")
print("pytest-Exit:", tests.returncode)
