import os
import threading
import time
from pathlib import Path

import pytest
import wx
from sceneaccess import gui


def _slow_search(*args, **kwargs):
    # Runs in the isolated worker process; tells the test when the lookup has started and finished.
    Path(os.environ['SCENEACCESS_TEST_LOOKUP_STARTED']).touch()
    time.sleep(.3)
    Path(os.environ['SCENEACCESS_TEST_LOOKUP_DONE']).touch()
    return []


def test_lookup_disables_other_windows_until_it_finishes(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, 'config_path_from_standard_paths', lambda _: tmp_path/'config.json')
    marker, done = tmp_path/'started', tmp_path/'done'
    monkeypatch.setenv('SCENEACCESS_TEST_LOOKUP_STARTED', str(marker))
    monkeypatch.setenv('SCENEACCESS_TEST_LOOKUP_DONE', str(done))
    app = wx.App(False)
    loop = wx.GUIEventLoop()
    activator = wx.EventLoopActivator(loop)
    frame, other = gui.MainFrame(), wx.Frame(None, title='Other chat')
    frame.Show()
    other.Show()
    observations = []

    def observe():
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        if marker.exists():
            wx.CallAfter(lambda: observations.append(
                (not done.exists(), frame.IsEnabled(), other.IsEnabled())))

    watcher = threading.Thread(target=observe, daemon=True)
    watcher.start()
    try:
        assert frame._lookup_with_progress(_slow_search, (), '') == []
        assert observations == [(True, False, False)]
        assert frame.IsEnabled() and other.IsEnabled()
    finally:
        watcher.join(6)
        other.Destroy()
        frame.Destroy()
        del activator, loop
        app.Destroy()


def _post_when_started(marker: Path, done: Path, heartbeat: list) -> None:
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    if marker.exists():
        # The app delivers worker results with wx.CallAfter, so that is what must get through, and
        # while the lookup still runs: delivered only afterwards, the GUI was blocked meanwhile.
        wx.CallAfter(lambda: heartbeat.append(not done.exists()))


@pytest.mark.parametrize('cancel', [False, True])
def test_old_source_lookup_pumps_gui_and_can_be_cancelled(tmp_path, monkeypatch, cancel):
    monkeypatch.setattr(gui, 'config_path_from_standard_paths', lambda _: tmp_path/'config.json')
    monkeypatch.setattr(gui, 'announce', lambda *a, **kw: None)
    monkeypatch.setattr(gui, 'search_videos', _slow_search)
    marker = tmp_path/'lookup-started'
    done = tmp_path/'lookup-done'
    monkeypatch.setenv('SCENEACCESS_TEST_LOOKUP_STARTED', str(marker))
    monkeypatch.setenv('SCENEACCESS_TEST_LOOKUP_DONE', str(done))
    app = wx.App(False)
    # The real app runs MainLoop; without an active loop nothing is delivered on Windows.
    loop = wx.GUIEventLoop()  # keep a reference: the activator does not own the loop
    activator = wx.EventLoopActivator(loop)
    frame = gui.MainFrame()
    source = tmp_path/'Video [abcdefghijk].mp4'
    source.write_bytes(b'fixture')
    frame.current = frame.store.create_session(owner_id=gui.OWNER_ID,source_path=source,
                                               title='Online video',model='fixture',initial_question='?')
    heartbeat = []
    watcher = threading.Thread(target=_post_when_started, args=(marker, done, heartbeat), daemon=True)
    if not cancel:
        watcher.start()
    class Choice:
        def __init__(self,*a,**kw): assert heartbeat == [True], 'GUI events were blocked by source lookup'
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def ShowModal(self): return wx.ID_CANCEL
    monkeypatch.setattr(gui.wx,'SingleChoiceDialog',Choice)
    if cancel:
        class Progress:
            def __init__(self,*a,**kw): pass
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def Pulse(self): return False, False
        monkeypatch.setattr(gui.wx,'GenericProgressDialog',Progress)
    try:
        started = time.monotonic()
        assert frame._find_online_source() is False
        assert time.monotonic()-started < 3
        if not cancel: assert heartbeat == [True]
    finally:
        if watcher.is_alive():
            watcher.join(6)
        frame.Destroy()
        del activator, loop
        app.Destroy()
