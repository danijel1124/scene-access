import subprocess
import sys
import threading
import time

import pytest

from sceneaccess.request_control import RequestControl, RequestCancelled


def _addition(a, b):
    return a + b


def _fail():
    raise ValueError('Fixture failure')


def _blocking_download(marker):
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    from pathlib import Path
    Path(marker).write_text(str(process.pid))
    process.wait()


def test_media_process_returns_value_and_propagates_error():
    from sceneaccess.worker_process import run_isolated_task
    assert run_isolated_task(_addition, (2, 3), control=RequestControl()) == 5
    with pytest.raises(RuntimeError, match='Fixture failure'):
        run_isolated_task(_fail, (), control=RequestControl())


def test_cancel_kills_media_worker_and_its_subprocess(tmp_path):
    from sceneaccess.worker_process import run_isolated_task
    marker = tmp_path/'pid.txt'
    control, errors, finished = RequestControl(), [], threading.Event()
    def work():
        try:
            run_isolated_task(_blocking_download, (str(marker),), control=control)
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()
    worker = threading.Thread(target=work)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        control.cancel()
        assert finished.wait(3)
        assert isinstance(errors[0], RequestCancelled)
        pid = int(marker.read_text())
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            api = ctypes.WinDLL('kernel32', use_last_error=True)
            api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            api.OpenProcess.restype = wintypes.HANDLE
            api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            api.GetExitCodeProcess.restype = wintypes.BOOL
            api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            api.WaitForSingleObject.restype = wintypes.DWORD
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            api.CloseHandle.restype = wintypes.BOOL
            handle = api.OpenProcess(0x1000 | 0x100000, False, pid)
            if handle:
                try:
                    assert api.WaitForSingleObject(handle, 3000) == 0
                    status = wintypes.DWORD()
                    assert api.GetExitCodeProcess(handle, ctypes.byref(status))
                    assert status.value != 259
                finally:
                    api.CloseHandle(handle)
            else:
                assert ctypes.get_last_error() == 87  # The process no longer exists.
        else:
            from pathlib import Path
            stat = Path(f'/proc/{pid}/stat')
            deadline = time.monotonic() + 3
            while True:
                try:
                    state = stat.read_text().split()[2]
                except (FileNotFoundError, ProcessLookupError):
                    break  # The child has exited and was reaped.
                if state == 'Z':
                    break
                assert time.monotonic() < deadline, f'Child is still running (state={state})'
                time.sleep(0.02)
    finally:
        control.cancel()
        worker.join(5)
