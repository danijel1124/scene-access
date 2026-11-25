import pytest
import wx

from sceneaccess.ui import chat_dialogs, dialogs


def test_start_chat_dialog_returns_the_question_and_profile():
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        dialog = chat_dialogs.StartChatDialog(frame, (("A", "Text A"), ("B", "Text B")), "B")
        assert dialog.value() == ("Describe this video.", "B")
        assert dialog.profile_text.GetValue() == "Text B"
        dialog.profile.SetSelection(0)
        dialog._show_profile()
        assert dialog.value() == ("Describe this video.", "")
        dialog.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


def test_start_chat_offers_each_tool_with_select_all_and_keeps_the_choice(monkeypatch):
    from sceneaccess.llm.tool_calls import HD_FRAME, VIDEO_INFO, WEB_SEARCH, ZOOM_FRAME
    app = wx.App(False)
    frame = wx.Frame(None)
    class Advanced:
        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def ShowModal(self): return wx.ID_OK
        def value(self): return 360, True, 25
    monkeypatch.setattr(chat_dialogs, "AdvancedDialog", Advanced)
    try:
        with chat_dialogs.StartChatDialog(frame, (), "") as dialog:
            # All tools of a local video are on by default; the YouTube tools are not offered.
            assert dialog.chosen_tools() == (ZOOM_FRAME, WEB_SEARCH)
            assert not dialog.tool_boxes[HD_FRAME].IsShown()
            assert dialog.toggle_tools.GetLabel() == "Deselect all tools"
            dialog._on_toggle_tools(None)
            assert dialog.chosen_tools() == () and dialog.toggle_tools.GetLabel() == "Select all tools"
            dialog.tool_boxes[ZOOM_FRAME].SetValue(True)
            dialog._on_advanced(None)
            assert dialog.chosen_tools() == (ZOOM_FRAME,)
            assert dialog.value() == ("Describe this video.", "")
        with chat_dialogs.StartChatDialog(frame, (), "", online=True, tools=(VIDEO_INFO,)) as dialog:
            assert dialog.tool_boxes[HD_FRAME].IsShown() and dialog.chosen_tools() == (VIDEO_INFO,)
            dialog._on_toggle_tools(None)
            assert dialog.chosen_tools() == ()
            dialog._on_toggle_tools(None)
            assert dialog.chosen_tools() == (ZOOM_FRAME, HD_FRAME, VIDEO_INFO, WEB_SEARCH)
    finally:
        frame.Destroy()
        app.Destroy()


def test_size_message_explains_the_sizes_and_offers_only_what_fits():
    online = chat_dialogs.size_message(46_000_000, "google/gemini-flash", online=True)
    assert "This video is 46.0 MB" in online and "adds about 15.3 MB, so 61.3 MB would be sent" in online
    assert "lower resolution" in online and "Don't show this dialog again" in online
    local = chat_dialogs.size_message(46_000_000, "openrouter/auto", online=False)
    assert "lower resolution" not in local and "Don't show this dialog again" not in local
    assert local.endswith("Shrink the video or send it as it is? Back returns to the Start a video chat dialog for these settings.")


def test_size_dialog_offers_lower_resolution_and_dont_ask_only_where_they_apply():
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        dialog = chat_dialogs.SizeDialog(frame, 46_000_000, "openrouter/auto", online=False)
        assert dialog.FindWindow(chat_dialogs.SIZE_LOWER) is None
        assert not dialog.dont_ask.IsShown()
        dialog.Destroy()
        dialog = chat_dialogs.SizeDialog(frame, 46_000_000, "google/gemini-flash", online=True, can_lower=False)
        assert not dialog.FindWindow(chat_dialogs.SIZE_LOWER).IsEnabled()
        assert dialog.FindWindow(chat_dialogs.SIZE_SHRINK).IsEnabled()
        assert dialog.dont_ask.IsShown()
        dialog.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


def test_start_chat_dialog_keeps_the_advanced_settings_and_a_previous_question():
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        dialog = chat_dialogs.StartChatDialog(frame, (("A", "Text A"),), "A", online=True, max_height=360,
                                              auto_shrink=True, shrink_target_mb=30, question="What is shown?")
        assert dialog.value() == ("What is shown?", "A")
        assert (dialog.max_height, dialog.auto_shrink, dialog.shrink_target_mb) == (360, True, 30)
        dialog.Destroy()
        advanced = chat_dialogs.AdvancedDialog(frame, False, 480, False, 18)
        assert not advanced.youtube_quality.IsShown()
        advanced.auto_shrink.SetValue(True)
        advanced.shrink_target.SetValue(25)
        assert advanced.value() == (480, True, 25)
        advanced.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


SETTINGS_MODELS = {"openrouter": "openrouter/auto", "llama.cpp": "default"}


def test_chat_settings_follow_settings_by_default_or_keep_an_own_model():
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        dialog = chat_dialogs.ChatSettingsDialog(frame, "", SETTINGS_MODELS, True, 61_000_000, 16,
                                                 {"openrouter": ["z-ai/glm-flash"]})
        assert dialog.use_default.GetLabel() == "Use the model from Settings (openrouter/auto)"
        assert dialog.value() == ("", True, False) and not dialog.model.IsEnabled()
        assert dialog.provider_value() == ("openrouter", "auto") and dialog.provider_note.GetLabel() == ""
        assert "61.0 MB once sent" in dialog.shrink.GetLabel()
        dialog.use_default.SetValue(False)
        dialog._update()
        dialog.model.SetValue("z-ai/glm-flash")
        dialog.audio_only.SetValue(False)
        dialog.shrink.SetValue(True)
        assert dialog.model.IsEnabled() and dialog.value() == ("z-ai/glm-flash", False, True)
        dialog.Destroy()
        own = chat_dialogs.ChatSettingsDialog(frame, "a/one", SETTINGS_MODELS, False, 1_000, 16)
        assert own.value() == ("a/one", False, False) and own.model.IsEnabled()
        own.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


def test_chat_settings_move_a_chat_to_the_other_provider(monkeypatch):
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        dialog = chat_dialogs.ChatSettingsDialog(frame, "a/one", SETTINGS_MODELS, False, 1_000, 16,
                                                 provider="openrouter", transcribe="on")
        assert dialog.audio_only.IsShown() and dialog.transcribe.IsShown()
        dialog.provider.SetSelection(1)
        dialog._on_provider()
        # A model name belongs to one provider: the chat starts with the other one's model from Settings.
        assert dialog.value() == ("", False, False) and dialog.model.GetValue() == "default"
        assert dialog.use_default.GetLabel() == "Use the model from Settings (default)"
        # The transcript mode does not depend on the provider.
        assert not dialog.audio_only.IsShown() and dialog.transcribe.IsShown()
        assert dialog.provider_value() == ("llama.cpp", "on")
        assert "llama.cpp server" in dialog.provider_note.GetLabel() and "ElevenLabs" in dialog.provider_note.GetLabel()
        dialog.transcribe.SetSelection(2)
        dialog._update()
        assert dialog.provider_value() == ("llama.cpp", "off") and "ElevenLabs" not in dialog.provider_note.GetLabel()
        dialog.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


@pytest.mark.parametrize("answer", [wx.YES, wx.NO])
def test_moving_a_llama_cpp_chat_to_openrouter_asks_first(monkeypatch, answer):
    app = wx.App(False)
    frame = wx.Frame(None)
    asked, skipped = [], []
    monkeypatch.setattr(chat_dialogs.wx, "MessageBox", lambda message, *a, **kw: asked.append(message) or answer)
    class Event:
        def Skip(self): skipped.append(True)
    try:
        dialog = chat_dialogs.ChatSettingsDialog(frame, "", SETTINGS_MODELS, False, 1_000, 16, provider="llama.cpp")
        dialog._on_ok(Event())
        assert asked == [] and skipped == [True]  # Staying on the own server asks nothing.
        dialog.provider.SetSelection(0)
        dialog._on_provider()
        assert "sent to OpenRouter" in dialog.provider_note.GetLabel()
        dialog._on_ok(Event())
        assert len(asked) == 1 and "OpenRouter" in asked[0]
        assert skipped == ([True, True] if answer == wx.YES else [True])
        dialog.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


def test_stored_size_dialog_offers_shrinking_and_chat_settings():
    message = chat_dialogs.stored_size_message(70_100_000, "openrouter/auto")
    assert message.startswith("This chat's stored video is 70.1 MB once sent")
    assert "costs more" in message and "Don't show this dialog again" not in message
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        dialog = chat_dialogs.StoredSizeDialog(frame, 70_100_000, "a/one")
        assert dialog.FindWindow(chat_dialogs.SIZE_CHAT_SETTINGS).GetLabel() == "Chat settings…"
        assert dialog.FindWindow(chat_dialogs.SIZE_SHRINK).GetLabel() == "Shrink the stored video"
        assert dialog.dont_ask.IsShown()
        dialog.Destroy()
        finder = dialogs.AddModelDialog(frame, ["google/gemini-flash"], title="Choose a model for this chat",
                                        allow_patterns=False)
        finder.filter.SetValue("google/*")
        assert finder.matches.GetStrings() == [] and finder.GetTitle() == "Choose a model for this chat"
        finder.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


def test_message_reader_dialog_shows_the_full_message_read_only():
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        dialog = chat_dialogs.MessageReaderDialog(frame, "Model: first line\nsecond line")
        assert dialog.text.GetValue() == "Model: first line\nsecond line"
        assert not dialog.text.IsEditable()
        dialog.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()
