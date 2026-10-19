import threading
import time

import wx

from sceneaccess import gui
from sceneaccess.openrouter import Completion, Turn


def test_reopened_chat_waits_for_old_worker_and_refreshes_after_save(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, 'config_path_from_standard_paths', lambda _: tmp_path/'config.json')
    monkeypatch.setattr(gui, 'announce', lambda *a, **kw: None)
    notices = []
    monkeypatch.setattr(gui.wx, 'MessageBox', lambda message, *a, **kw: notices.append(message) or wx.OK)
    monkeypatch.setattr(gui, 'needs_transcript', lambda *a: False)
    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', lambda *a, **kw:
                        Turn(entries=[{'role':'assistant','content':'Saved answer'}],
                             completion=Completion('Saved answer','fixture',0,None,None)))
    app = wx.App(False)
    windows = gui.ChatWindows()
    entered, release, saved = threading.Event(), threading.Event(), threading.Event()
    try:
        windows.new_window()  # Keep the launcher alive after the chat closes.
        old = windows.new_window()
        old.config = gui.replace(old.config, api_key='test-only')
        source = tmp_path/'source.mp4'
        source.write_bytes(b'fixture')
        old.current = old.store.create_session(owner_id=gui.OWNER_ID, source_path=source,
                                               title='Chat', model='fixture', initial_question='?')
        sid = old.current.id
        original = old.store.record_turn
        def paused_save(*args):
            entered.set()
            assert release.wait(5)
            original(*args)
            saved.set()
        monkeypatch.setattr(old.store, 'record_turn', paused_save)
        old._send('first', initial=False)
        assert entered.wait(3)
        old.Close()
        wx.Yield()
        reopened = windows.open_session(sid)
        reopened._send('second', initial=False)
        assert reopened._request is None
        assert reopened.question.GetValue() == 'second'
        assert notices and 'previous request' in notices[-1].lower()
        release.set()
        assert saved.wait(3)
        deadline = time.monotonic()+3
        while time.monotonic() < deadline and sid in windows.requests:
            wx.Yield()
            time.sleep(.01)
        wx.Yield()
        assert sid not in windows.requests
        assert any(item['content']=='Saved answer' for item in reopened.current.history)
    finally:
        release.set()
        saved.wait(3)
        wx.Yield()
        for frame in list(windows.frames): frame.Destroy()
        app.Destroy()
