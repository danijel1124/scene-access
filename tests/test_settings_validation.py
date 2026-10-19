from dataclasses import replace

import wx

from sceneaccess import gui
from sceneaccess.config import AppConfig


def test_settings_reject_ambiguous_api_address_without_saving(tmp_path, monkeypatch):
    app = wx.App(False)
    path = tmp_path/'config.json'
    monkeypatch.setattr(gui, 'config_path_from_standard_paths', lambda _: path)
    frame = gui.MainFrame()
    messages = []
    class Dialog:
        def __init__(self, *args): pass
        def ShowModal(self): return wx.ID_OK
        def value(self): return replace(AppConfig(), base_url='https://example.test/custom')
        def Destroy(self): pass
    monkeypatch.setattr(gui, 'SettingsDialog', Dialog)
    monkeypatch.setattr(gui.wx, 'MessageBox', lambda message, *a, **k: messages.append(message))
    try:
        original = frame.config
        frame.on_settings(None)
        assert frame.config is original
        assert messages and 'chat/completions' in messages[0]
        assert not path.exists()
    finally:
        frame.Destroy()
        app.Destroy()
