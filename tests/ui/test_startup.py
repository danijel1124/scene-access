import pytest
import wx

from sceneaccess.ui import app, app_window


def test_invalid_config_reports_path_and_exits_without_modifying_file(tmp_path, monkeypatch):
    config = tmp_path/'config.json'
    config.write_text('[]', encoding='utf-8')
    shown = []
    monkeypatch.setattr(app_window, 'config_path_from_standard_paths', lambda _: config)
    monkeypatch.setattr(wx, 'MessageBox', lambda text, *a, **kw: shown.append(text) or wx.OK)
    try:
        assert app.main() == 1
        assert shown and str(config) in shown[-1]
        assert config.read_text(encoding='utf-8') == '[]'
        assert not (tmp_path/'instance.port').exists()
    finally:
        for frame in wx.GetTopLevelWindows(): frame.Destroy()
        wx_app = wx.GetApp()
        if wx_app: wx_app.Destroy()


@pytest.mark.parametrize('last', ['list', 'chat', 'starting chat'])
def test_activating_application_restores_the_window_used_last(tmp_path, monkeypatch, last):
    monkeypatch.setattr(app_window, 'config_path_from_standard_paths', lambda _: tmp_path/'config.json')
    wx_app = wx.App(False)
    manager = app.ChatWindows()
    try:
        chat_list, chat = manager.show_list(), manager.new_chat_window()
        if last != 'starting chat':
            chat.Show()  # A chat window still starting stays hidden until its chat exists.
        raised = []
        for frame in (chat_list, chat):
            monkeypatch.setattr(frame, 'Raise', lambda frame=frame: raised.append(frame))
            monkeypatch.setattr(frame, 'SetFocus', lambda: None)
        manager.last_active = chat_list if last == 'list' else chat
        manager.activate()
        assert raised == [chat if last == 'chat' else chat_list]
    finally:
        for frame in list(manager.all_windows()): frame.Destroy()
        wx_app.Destroy()
