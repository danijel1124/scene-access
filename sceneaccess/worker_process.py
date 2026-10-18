"""Isolate blocking media libraries in an interruptible process tree.

POSIX uses a separate session/process group. Windows uses a native Job Object,
so cancelling a yt-dlp worker also stops the ffmpeg/JavaScript children it owns.
The worker waits for the parent to attach cancellation before doing any work.
"""
from __future__ import annotations

import multiprocessing
import os
import signal
import time
import threading
from typing import Any, Callable

from .request_control import RequestControl


def _worker(pipe, ready, function, args, kwargs) -> None:
    try:
        if os.name != 'nt':
            os.setsid()
        pipe.send(('ready', None))
        ready.wait()
        result = function(*args, **kwargs)
        pipe.send(('result', result))
    except Exception as error:
        pipe.send(('error', str(error)))
    finally:
        pipe.close()


class _WindowsJob:
    def __init__(self, process_handle: int):
        import ctypes
        from ctypes import wintypes
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.TerminateJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.api.AssignProcessToJobObject(self.handle, process_handle):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def terminate(self) -> None:
        if self.handle:
            self.api.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def run_isolated_task(function: Callable[..., Any], args: tuple, *, control: RequestControl,
                   kwargs: dict | None = None, timeout: float = 300) -> Any:
    """Run a picklable media operation; cancellation terminates its entire process tree."""
    control.check_cancelled()
    context = multiprocessing.get_context('spawn')
    receiving, sending = context.Pipe(duplex=False)
    ready = context.Event()
    process = context.Process(target=_worker, args=(sending, ready, function, args, kwargs or {}), daemon=True)
    job = None
    group_ready = False
    interrupt_lock = threading.Lock()
    active = True
    deadline = time.monotonic() + timeout

    def interrupt() -> None:
        with interrupt_lock:
            if not active:
                return
            if job is not None:
                job.terminate()
            elif group_ready:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.is_alive():
                process.terminate()

    process.start()
    sending.close()
    try:
        while True:
            control.check_cancelled()
            if time.monotonic() >= deadline:
                raise TimeoutError('The media operation timed out.')
            if not receiving.poll(0.05):
                if not process.is_alive():
                    raise RuntimeError('The media worker exited without a result.')
                continue
            try:
                kind, value = receiving.recv()
            except EOFError:
                control.check_cancelled()
                raise RuntimeError('The media worker exited without a result.') from None
            if kind == 'ready':
                if os.name == 'nt':
                    job = _WindowsJob(process.sentinel)
                else:
                    group_ready = True
                control.register_interrupt(interrupt)
                control.check_cancelled()
                ready.set()
            elif kind == 'error':
                raise RuntimeError(value)
            elif kind == 'result':
                control.check_cancelled()
                return value
    finally:
        control.register_interrupt(None)
        interrupt()
        process.join(3)
        if process.is_alive():
            process.kill()
            process.join()
        receiving.close()
        with interrupt_lock:
            active = False
            if job is not None:
                job.close()
            process.close()
