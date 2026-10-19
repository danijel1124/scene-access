import os
import time

import pytest
import wx
from sceneaccess import gui


def _slow_search(*args, **kwargs):
    time.sleep(.3)
    return []


@pytest.mark.parametrize('cancel', [False, True])
def test_old_source_lookup_pumps_gui_and_can_be_cancelled(tmp_path, monkeypatch, cancel):
    monkeypatch.setattr(gui, 'config_path_from_standard_paths', lambda _: tmp_path/'config.json')
    monkeypatch.setattr(gui, 'announce', lambda *a, **kw: None)
    monkeypatch.setattr(gui, 'search_videos', _slow_search)
    app = wx.App(False)
    frame = gui.MainFrame()
    source = tmp_path/'Video [abcdefghijk].mp4'
    source.write_bytes(b'fixture')
    frame.current = frame.store.create_session(owner_id=gui.OWNER_ID,source_path=source,
                                               title='Online video',model='fixture',initial_question='?')
    heartbeat = []
    timer = wx.CallLater(30, lambda: heartbeat.append(True))
    class Choice:
        def __init__(self,*a,**kw): assert heartbeat, 'GUI heartbeat was blocked by source lookup'
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
        if not cancel: assert heartbeat
    finally:
        timer.Stop()
        frame.Destroy()
        app.Destroy()
