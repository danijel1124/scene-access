import wx

from sceneaccess.config import AppConfig
from sceneaccess import gui
from sceneaccess.gui import SettingsDialog, chat_history_items, usage_text


def test_enter_sends_the_question_and_shift_enter_keeps_the_line_break(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "config_path_from_standard_paths", lambda _paths: tmp_path / "config.json")
    app = wx.App(False)
    frame = gui.MainFrame()
    sent, opened = [], []
    monkeypatch.setattr(frame, "on_send", lambda _event: sent.append(True))
    monkeypatch.setattr(frame, "open_selected_message", lambda: opened.append(True))
    try:
        assert frame.handle_enter(frame.question, wx.WXK_RETURN, shift=True) is False
        assert sent == []
        assert frame.handle_enter(frame.question, wx.WXK_RETURN, shift=False) is True
        assert frame.handle_enter(frame.question, wx.WXK_NUMPAD_ENTER, shift=False) is True
        assert sent == [True, True]
        assert frame.handle_enter(frame.transcript, wx.WXK_RETURN, shift=False) is True
        assert opened == [True]
        assert frame.handle_enter(frame.sessions, wx.WXK_RETURN, shift=False) is False
        assert frame.handle_enter(frame.question, ord("A"), shift=False) is False
    finally:
        frame.Destroy()
        app.Destroy()


def test_new_video_chat_menu_runs_the_chosen_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "config_path_from_standard_paths", lambda _paths: tmp_path / "config.json")
    app = wx.App(False)
    frame = gui.MainFrame()
    calls = []
    monkeypatch.setattr(frame, "new_from_local_file", lambda: calls.append("local"))
    monkeypatch.setattr(frame, "new_from_youtube", lambda: calls.append("youtube"))
    try:
        for position, expected in ((0, "local"), (1, "youtube")):
            monkeypatch.setattr(frame.new_button, "GetPopupMenuSelectionFromUser",
                                lambda menu, _pos, p=position: menu.FindItemByPosition(p).GetId())
            frame.on_new(None)
            assert calls[-1] == expected
        monkeypatch.setattr(frame.new_button, "GetPopupMenuSelectionFromUser", lambda _menu, _pos: wx.ID_NONE)
        frame.on_new(None)
        assert calls == ["local", "youtube"]
    finally:
        frame.Destroy()
        app.Destroy()


def test_default_download_folder_is_created_inside_an_existing_videos_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "config_path_from_standard_paths", lambda _paths: tmp_path / "config.json")
    monkeypatch.setattr(gui, "default_download_dir", lambda: tmp_path / "Videos" / "scene-access")
    (tmp_path / "Videos").mkdir()
    app = wx.App(False)
    frame = gui.MainFrame()
    try:
        assert frame._download_dir() == tmp_path / "Videos" / "scene-access"
        assert (tmp_path / "Videos" / "scene-access").is_dir()
    finally:
        frame.Destroy()
        app.Destroy()


def test_filename_dialog_toggles_parts_and_resets_to_the_yt_dlp_default():
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        dialog = gui.FilenameDialog(frame, ("title", "id"))
        assert dialog.value() == ("title", "id")
        assert dialog.template.GetLabel() == "Template: %(title)s [%(id)s].%(ext)s"
        dialog.parts.Check(gui.FILENAME_FIELD_NAMES.index("uploader"))
        dialog._show_template()
        assert dialog.value() == ("title", "id", "uploader")
        assert dialog.template.GetLabel() == "Template: %(title)s [%(id)s] [%(uploader)s].%(ext)s"
        dialog._on_reset(None)
        assert dialog.value() == ("title", "id")
        dialog.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


def test_message_reader_dialog_shows_the_full_message_read_only():
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        dialog = gui.MessageReaderDialog(frame, "Model: first line\nsecond line")
        assert dialog.text.GetValue() == "Model: first line\nsecond line"
        assert not dialog.text.IsEditable()
        dialog.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


def test_settings_dialog_can_be_constructed_and_returns_its_values():
    app = wx.App(False)
    frame = wx.Frame(None)
    expected = AppConfig(model="openrouter/auto", base_url="https://router.example/api", api_key="secret-for-test-only")
    try:
        dialog = SettingsDialog(frame, expected)
        assert dialog.value() == expected
        dialog.youtube_quality.SetSelection(dialog.heights.index(720))
        assert dialog.value().youtube_max_height == 720
        dialog.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


def test_usage_text_reports_hit_miss_and_write_with_the_answering_model():
    usage = {"model": "z-ai/glm-4.5v", "prompt_tokens": 9175, "cached_tokens": 9137, "cache_write_tokens": 0}

    assert usage_text(usage) == (
        "Usage: model z-ai/glm-4.5v | prompt tokens: 9,175 | cache read (hit): 9,137"
        " | not from cache (miss): 38 | cache write: 0"
    )


def test_usage_text_does_not_claim_zero_hits_when_provider_reported_nothing():
    usage = {"model": "openrouter/auto", "prompt_tokens": 9175, "cached_tokens": None, "cache_write_tokens": None}

    assert usage_text(usage) == (
        "Usage: model openrouter/auto | prompt tokens: 9,175 | cache details were not reported by the provider"
    )


def test_chat_history_lists_each_message_with_usage_below_every_answer():
    history = [
        {"role": "assistant", "content": "A jacket.", "usage": {"model": "m", "prompt_tokens": 10, "cached_tokens": 0, "cache_write_tokens": 10}},
        {"role": "user", "content": "Is it glossy?"},
        {"role": "assistant", "content": "No."},
    ]

    assert chat_history_items("Describe it.", history) == [
        "You: Describe it.",
        "Model: A jacket.",
        "Usage: model m | prompt tokens: 10 | cache read (hit): 0 | not from cache (miss): 10 | cache write: 10",
        "You: Is it glossy?",
        "Model: No.",
        "Usage: no data was recorded for this answer.",
    ]
