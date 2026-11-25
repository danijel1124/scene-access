from dataclasses import replace

import wx

import pytest

from sceneaccess.ui import app, app_window, chat_list
from sceneaccess.settings.config import AppConfig
from sceneaccess.providers import LLAMA_CPP


@pytest.mark.parametrize('settings, expected', [
    (replace(AppConfig(), llama_cpp_url='https://example.test/custom'), 'chat/completions'),
    (replace(AppConfig(), provider=LLAMA_CPP), 'URL is required'),
])
def test_settings_reject_an_unusable_llama_cpp_address_without_saving(tmp_path, monkeypatch, settings, expected):
    wx_app = wx.App(False)
    path = tmp_path/'config.json'
    monkeypatch.setattr(app_window, 'config_path_from_standard_paths', lambda _: path)
    windows = app.ChatWindows()
    frame = windows.show_list()
    messages = []
    class Dialog:
        def __init__(self, *args): pass
        def ShowModal(self): return wx.ID_OK
        def value(self): return settings
        def Destroy(self): pass
    monkeypatch.setattr(chat_list, 'SettingsDialog', Dialog)
    monkeypatch.setattr(chat_list.wx, 'MessageBox', lambda message, *a, **k: messages.append(message))
    try:
        original = frame.config
        frame.on_settings(None)
        assert frame.config is original
        assert messages and expected in messages[0]
        assert not path.exists()
    finally:
        frame.Destroy()
        wx_app.Destroy()
