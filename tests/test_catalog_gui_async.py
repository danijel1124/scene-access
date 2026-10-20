import threading
import time

import wx
import pytest
from sceneaccess import gui, dialogs
from sceneaccess.config import AppConfig
from sceneaccess.models import api_url


@pytest.mark.parametrize('which', ['chat', 'exclusions'])
def test_settings_catalog_lookup_does_not_block_gui(tmp_path, monkeypatch, which):
    monkeypatch.setattr(gui, 'config_path_from_standard_paths', lambda _: tmp_path/'config.json')
    app = wx.App(False)
    # The real app runs MainLoop; without an active loop wx.Yield delivers nothing on Windows.
    loop = wx.GUIEventLoop()  # keep a reference: the activator does not own the loop
    activator = wx.EventLoopActivator(loop)
    frame = gui.MainFrame()
    queried, proceed, completed = threading.Event(), threading.Event(), threading.Event()
    threads = []
    class Catalog:
        url = api_url(AppConfig.base_url, 'models')
        def model_ids(self):
            threads.append(wx.IsMainThread())
            queried.set()
            proceed.wait(.4)
            return ['fixture']
        def providers(self): return []
    class ResultDialog:
        def __init__(self, *args): completed.set()
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def ShowModal(self): return wx.ID_CANCEL
    catalog = Catalog()
    settings = None
    try:
        if which == 'chat':
            source = tmp_path/'source.mp4'
            source.write_bytes(b'fixture')
            frame.current = frame.store.create_session(owner_id=gui.OWNER_ID, source_path=source,
                                                       title='T', model='fixture', initial_question='?')
            frame.catalog = catalog
            monkeypatch.setattr(gui, 'ChatSettingsDialog', ResultDialog)
            action = frame.change_chat_settings
        else:
            settings = dialogs.SettingsDialog(frame, AppConfig(), catalog)
            monkeypatch.setattr(dialogs, 'ExclusionsDialog', ResultDialog)
            action = lambda: settings._on_exclusions(None)
        started = time.monotonic()
        action()
        assert time.monotonic()-started < .2
        assert queried.wait(1)
        assert threads == [False]
        assert not completed.is_set()
        proceed.set()
        deadline = time.monotonic()+2
        while not completed.is_set() and time.monotonic()<deadline:
            wx.Yield()
            time.sleep(.005)
        assert completed.is_set()
    finally:
        proceed.set()
        if settings: settings.Destroy()
        frame.Destroy()
        del activator, loop
        app.Destroy()
