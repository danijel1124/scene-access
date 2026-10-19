"""Real-process regressions for recovery of the interrupted implementation."""
import sys
import threading
import time

from sceneaccess import video_size


def test_shrink_drains_stderr_while_process_runs(monkeypatch):
    command = [sys.executable, '-c', "import sys; sys.stderr.write('x'*2000000+'END-MARKER'); sys.stderr.flush(); sys.exit(1)"]
    monkeypatch.setattr(video_size, '_ffmpeg_command', lambda *_: command)
    cancel, done = threading.Event(), threading.Event()
    errors = []
    def work():
        try:
            video_size._run('unused', [], 1, lambda _: None, cancel.is_set)
        except Exception as error:
            errors.append(error)
        finally:
            done.set()
    thread = threading.Thread(target=work)
    thread.start()
    try:
        assert done.wait(3), 'stderr pipe filled and blocked the encoder'
        assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
        assert 'END-MARKER' in str(errors[0])
        assert len(str(errors[0])) < 100000
    finally:
        cancel.set()
        thread.join(3)


def test_shrink_cancels_already_running_silent_process(tmp_path, monkeypatch):
    started = tmp_path/'started'
    command = [sys.executable, '-c', f"from pathlib import Path; import time; Path({str(started)!r}).touch(); time.sleep(20)"]
    monkeypatch.setattr(video_size, '_ffmpeg_command', lambda *_: command)
    cancel, done = threading.Event(), threading.Event()
    errors = []
    def work():
        try:
            video_size._run('unused', [], 1, lambda _: None, cancel.is_set)
        except Exception as error:
            errors.append(error)
        finally:
            done.set()
    thread = threading.Thread(target=work)
    thread.start()
    try:
        deadline = time.monotonic()+3
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert started.exists()
        cancel.set()
        assert done.wait(2)
        assert len(errors) == 1 and isinstance(errors[0], video_size.ShrinkCancelled)
    finally:
        cancel.set()
        thread.join(3)
