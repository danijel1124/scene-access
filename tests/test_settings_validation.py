from dataclasses import replace

import wx

import pytest

from sceneaccess import gui
from sceneaccess.config import AppConfig
from sceneaccess.providers import LLAMA_CPP


@pytest.mark.parametrize('settings, expected', [
    (replace(AppConfig(), llama_cpp_url='https://example.test/custom'), 'chat/completions'),
    (replace(AppConfig(), provider=LLAMA_CPP), 'URL is required'),
])
def test_settings_reject_an_unusable_llama_cpp_address_without_saving(tmp_path, monkeypatch, settings, expected):
    app = wx.App(False)
    path = tmp_path/'config.json'
    monkeypatch.setattr(gui, 'config_path_from_standard_paths', lambda _: path)
    frame = gui.MainFrame()
    messages = []
    class Dialog:
        def __init__(self, *args): pass
        def ShowModal(self): return wx.ID_OK
        def value(self): return settings
        def Destroy(self): pass
    monkeypatch.setattr(gui, 'SettingsDialog', Dialog)
    monkeypatch.setattr(gui.wx, 'MessageBox', lambda message, *a, **k: messages.append(message))
    try:
        original = frame.config
        frame.on_settings(None)
        assert frame.config is original
        assert messages and expected in messages[0]
        assert not path.exists()
    finally:
        frame.Destroy()
        app.Destroy()
