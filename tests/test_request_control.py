import threading

import pytest


def test_cancelling_ffmpeg_terminates_the_process(monkeypatch):
    import subprocess
    import sys
    import threading
    from sceneaccess import tools
    from sceneaccess.request_control import RequestControl, RequestCancelled
    real_popen = subprocess.Popen
    started, done = threading.Event(), threading.Event()
    processes, errors = [], []
    def start(*args, **kwargs):
        process = real_popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        processes.append(process)
        started.set()
        return process
    monkeypatch.setattr(tools, '_ffmpeg', lambda: sys.executable)
    monkeypatch.setattr(tools.subprocess, 'Popen', start)
    control = RequestControl()
    def work():
        try:
            tools.run_ffmpeg([], control=control)
        except Exception as error:
            errors.append(error)
        finally:
            done.set()
    thread = threading.Thread(target=work)
    thread.start()
    try:
        assert started.wait(2)
        control.cancel()
        assert done.wait(3)
        assert isinstance(errors[0], RequestCancelled)
        assert processes[0].poll() is not None
    finally:
        for process in processes:
            if process.poll() is None: process.kill()
        thread.join(3)


def test_cancelled_transcription_does_not_extract_or_send_audio():
    from sceneaccess.transcript import make_transcript
    from sceneaccess.openrouter import OpenRouterClient
    from sceneaccess.request_control import RequestControl, RequestCancelled
    control = RequestControl()
    control.cancel()
    with pytest.raises(RequestCancelled):
        make_transcript(None, OpenRouterClient('fixture', control=control))


def test_cancel_interrupts_registered_io_and_rejects_more_work():
    from sceneaccess.request_control import RequestControl, RequestCancelled
    control = RequestControl()
    interrupted = []
    control.register_interrupt(lambda: interrupted.append(True))
    control.cancel()
    assert interrupted == [True]
    with pytest.raises(RequestCancelled):
        control.check_cancelled()


def test_pause_waits_at_boundary_and_resume_continues():
    from sceneaccess.request_control import RequestControl
    control = RequestControl()
    paused, finished = threading.Event(), threading.Event()
    control.pause()
    thread = threading.Thread(target=lambda: (control.checkpoint(lambda: paused.set()), finished.set()))
    thread.start()
    try:
        assert paused.wait(2)
        assert not finished.is_set()
        control.resume()
        assert finished.wait(2)
    finally:
        control.cancel()
        thread.join(2)


def test_cancel_wakes_a_paused_request():
    from sceneaccess.request_control import RequestControl, RequestCancelled
    control = RequestControl()
    paused, cancelled = threading.Event(), threading.Event()
    control.pause()
    def worker():
        try:
            control.checkpoint(lambda: paused.set())
        except RequestCancelled:
            cancelled.set()
    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert paused.wait(2)
        control.cancel()
        assert cancelled.wait(2)
    finally:
        control.cancel()
        thread.join(2)
