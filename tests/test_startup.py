import wx

from sceneaccess import gui


def test_invalid_config_reports_path_and_exits_without_modifying_file(tmp_path, monkeypatch):
    config = tmp_path/'config.json'
    config.write_text('[]', encoding='utf-8')
    shown = []
    monkeypatch.setattr(gui, 'config_path_from_standard_paths', lambda _: config)
    monkeypatch.setattr(gui.wx, 'MessageBox', lambda text, *a, **kw: shown.append(text) or wx.OK)
    try:
        assert gui.main() == 1
        assert shown and str(config) in shown[-1]
        assert config.read_text(encoding='utf-8') == '[]'
        assert not (tmp_path/'instance.port').exists()
    finally:
        for frame in wx.GetTopLevelWindows(): frame.Destroy()
        app = wx.GetApp()
        if app: app.Destroy()


def test_activating_application_restores_last_chat_window(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, 'config_path_from_standard_paths', lambda _: tmp_path/'config.json')
    app = wx.App(False)
    manager = gui.ChatWindows()
    try:
        first, second = manager.new_window(), manager.new_window()
        raised = []
        monkeypatch.setattr(first, 'Raise', lambda: raised.append(first))
        monkeypatch.setattr(first, 'SetFocus', lambda: None)
        manager.last_active = first
        manager.activate()
        assert raised == [first]
    finally:
        for frame in list(manager.frames): frame.Destroy()
        app.Destroy()
