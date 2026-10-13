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
