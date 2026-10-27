"""A legacy chat must never persist a collection as its single video source."""
import wx
import pytest

from sceneaccess import gui
from test_playlist_rejection import video_server


@pytest.mark.parametrize('path', ['/index.html', '/one.mp4', '/single.html'])
def test_legacy_source_only_persists_single_video(tmp_path, video_server, monkeypatch, path):
    base_url, requests, _payload = video_server
    notices = []
    monkeypatch.setattr(gui, 'config_path_from_standard_paths', lambda _: tmp_path / 'config.json')
    monkeypatch.setattr(gui, 'announce', lambda *a, **kw: None)
    monkeypatch.setattr(gui, 'search_videos', lambda *a, **kw: [])
    monkeypatch.setattr(gui, 'url_from_clipboard', lambda: '')
    monkeypatch.setattr(gui.wx, 'MessageBox', lambda message, *a, **kw: notices.append(message) or wx.OK)

    class EnterLink:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def ShowModal(self): return wx.ID_OK
        def GetSelection(self): return 0  # Enter a link, with no search results.
        def GetValue(self): return base_url + path

    monkeypatch.setattr(gui.wx, 'SingleChoiceDialog', EnterLink)
    monkeypatch.setattr(gui.wx, 'TextEntryDialog', EnterLink)
    app = wx.App(False)
    loop = wx.GUIEventLoop()
    activator = wx.EventLoopActivator(loop)
    frame = gui.MainFrame()
    try:
        source = tmp_path / 'fixture.mp4'
        source.write_bytes(b'fixture')
        frame.current = frame.store.create_session(owner_id=gui.OWNER_ID, source_path=source,
                                                   title='Old online chat', model='fixture', initial_question='?')
        before = frame.store.get_session(frame.current.id, gui.OWNER_ID)
        monkeypatch.setattr(frame, '_lookup_with_progress', lambda f, args, cookies: f(*args, cookies=cookies))
        assert frame._find_online_source() is True  # The chat remains usable without a source.
        after = frame.store.get_session(frame.current.id, gui.OWNER_ID)
        if path == '/index.html':
            assert after.source_url == before.source_url
            assert after.video_info == before.video_info
            assert frame.current.source_url == before.source_url
            assert any('single video' in message for message in notices)
            assert '/one.mp4' not in requests and '/two.mp4' not in requests
        else:
            assert after.source_url == frame.current.source_url == base_url + path
            assert after.video_info and after.video_info.get('title')
            assert notices == []
        assert path in requests
    finally:
        frame.Destroy()
        del activator, loop
        app.Destroy()
