import wx

from sceneaccess import dialogs
from sceneaccess.config import AppConfig
from sceneaccess.dialogs import SettingsDialog
from sceneaccess.youtube import FILENAME_FIELD_NAMES


def test_start_chat_dialog_returns_the_question_and_profile():
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        dialog = dialogs.StartChatDialog(frame, (("A", "Text A"), ("B", "Text B")), "B")
        assert dialog.value() == ("Describe this video.", "B")
        assert dialog.profile_text.GetValue() == "Text B"
        dialog.profile.SetSelection(0)
        dialog._show_profile()
        assert dialog.value() == ("Describe this video.", "")
        dialog.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


def test_profiles_dialog_adds_renames_and_deletes_profiles():
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        dialog = dialogs.ProfilesDialog(frame, (("A", "Text A"),))
        dialog._on_add(None)
        dialog.name.SetValue("Mine")
        dialog.text.SetValue("My text")
        assert dialog.value() == (("A", "Text A"), ("Mine", "My text"))
        assert dialog.names.GetString(1) == "Mine"
        dialog.names.SetSelection(0)
        dialog._on_delete(None)
        assert dialog.value() == (("Mine", "My text"),)
        dialog.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


def test_settings_keep_the_profiles_and_the_profile_chosen_last():
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        config = AppConfig(profiles=(("Mine", "My text"),), profile="Mine", youtube_cookies="firefox", audio_only_auto=True)
        dialog = SettingsDialog(frame, config)
        assert dialog.value().profiles == (("Mine", "My text"),)
        assert dialog.value().profile == "Mine"
        assert dialog.value().youtube_cookies == "firefox" and dialog.value().audio_only_auto is True
        assert dialog.cookies_button.GetLabel() == "Firefox (change…)"
        dialog.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


def test_exclusions_dialog_toggles_providers_and_adds_and_removes_models():
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        providers = [("Google AI Studio", "google-ai-studio"), ("Novita", "novita")]
        # "old-provider" is no longer in OpenRouter's list but stays excluded and visible.
        dialog = dialogs.ExclusionsDialog(frame, providers, ("novita", "old-provider"), ["a/one"], ("google/*",))
        assert dialog.provider_list.GetString(0) == "Google AI Studio (google-ai-studio)"
        assert dialog.provider_list.GetString(2) == "old-provider"
        assert dialog.value() == (("novita", "old-provider"), ("google/*",))
        dialog.provider_list.Check(0)
        dialog.provider_list.Check(1, False)
        dialog.add_model("a/one")
        dialog.add_model("a/one")
        assert dialog.value() == (("google-ai-studio", "old-provider"), ("google/*", "a/one"))
        dialog.model_list.SetSelection(0)
        dialog._on_remove(None)
        assert dialog.value()[1] == ("a/one",)
        dialog.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


def test_add_model_dialog_filters_models_and_offers_typed_patterns():
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        dialog = dialogs.AddModelDialog(frame, ["google/gemini-flash", "google/gemini-pro", "z-ai/glm-flash"])
        assert dialog.value() == ""
        dialog.filter.SetValue("FLASH")
        assert dialog.matches.GetStrings() == ["google/gemini-flash", "z-ai/glm-flash"]
        assert dialog.value() == "google/gemini-flash"
        assert dialog.list_label.GetLabel() == "Matching models (2)"
        dialog.filter.SetValue("google/*")
        assert dialog.matches.GetStrings() == ["Pattern: google/*"]
        assert dialog.value() == "google/*"
        dialog.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


def test_settings_show_and_keep_the_exclusions():
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        config = AppConfig(ignored_providers=("google-ai-studio",), excluded_models=("a/one", "b/two"))
        dialog = SettingsDialog(frame, config)
        assert dialog.exclusions_button.GetLabel() == "1 provider and 2 models excluded (change…)"
        assert dialog.value().ignored_providers == ("google-ai-studio",)
        assert dialog.value().excluded_models == ("a/one", "b/two")
        dialog.Destroy()
        assert dialogs.exclusions_description((), ()) == "No providers and no models excluded"
    finally:
        frame.Destroy()
        app.Destroy()


def test_size_message_explains_the_sizes_and_offers_only_what_fits():
    online = dialogs.size_message(46_000_000, "google/gemini-flash", online=True)
    assert "This video is 46.0 MB" in online and "adds about 15.3 MB, so 61.3 MB would be sent" in online
    assert "lower resolution" in online and "Don't show this dialog again" in online
    local = dialogs.size_message(46_000_000, "openrouter/auto", online=False)
    assert "lower resolution" not in local and "Don't show this dialog again" not in local
    assert local.endswith("Shrink the video or send it as it is? Back returns to the Start a video chat dialog for these settings.")


def test_size_dialog_offers_lower_resolution_and_dont_ask_only_where_they_apply():
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        dialog = dialogs.SizeDialog(frame, 46_000_000, "openrouter/auto", online=False)
        assert dialog.FindWindow(dialogs.SIZE_LOWER) is None
        assert not dialog.dont_ask.IsShown()
        dialog.Destroy()
        dialog = dialogs.SizeDialog(frame, 46_000_000, "google/gemini-flash", online=True, can_lower=False)
        assert not dialog.FindWindow(dialogs.SIZE_LOWER).IsEnabled()
        assert dialog.FindWindow(dialogs.SIZE_SHRINK).IsEnabled()
        assert dialog.dont_ask.IsShown()
        dialog.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


def test_start_chat_dialog_keeps_the_advanced_settings_and_a_previous_question():
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        dialog = dialogs.StartChatDialog(frame, (("A", "Text A"),), "A", online=True, max_height=360, auto_shrink=True,
                                         shrink_target_mb=30, question="What is shown?")
        assert dialog.value() == ("What is shown?", "A")
        assert (dialog.max_height, dialog.auto_shrink, dialog.shrink_target_mb) == (360, True, 30)
        dialog.Destroy()
        advanced = dialogs.AdvancedDialog(frame, False, 480, False, 18)
        assert not advanced.youtube_quality.IsShown()
        advanced.auto_shrink.SetValue(True)
        advanced.shrink_target.SetValue(25)
        assert advanced.value() == (480, True, 25)
        advanced.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


def test_chat_settings_follow_settings_by_default_or_keep_an_own_model():
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        dialog = dialogs.ChatSettingsDialog(frame, "", "openrouter/auto", True, 61_000_000, 16, ["z-ai/glm-flash"])
        assert dialog.use_default.GetLabel() == "Use the model from Settings (openrouter/auto)"
        assert dialog.value() == ("", True, False) and not dialog.model.IsEnabled()
        assert "61.0 MB once sent" in dialog.shrink.GetLabel()
        dialog.use_default.SetValue(False)
        dialog._update()
        dialog.model.SetValue("z-ai/glm-flash")
        dialog.audio_only.SetValue(False)
        dialog.shrink.SetValue(True)
        assert dialog.model.IsEnabled() and dialog.value() == ("z-ai/glm-flash", False, True)
        dialog.Destroy()
        own = dialogs.ChatSettingsDialog(frame, "a/one", "openrouter/auto", False, 1_000, 16)
        assert own.value() == ("a/one", False, False) and own.model.IsEnabled()
        own.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


def test_stored_size_dialog_offers_shrinking_and_chat_settings():
    message = dialogs.stored_size_message(70_100_000, "openrouter/auto")
    assert message.startswith("This chat's stored video is 70.1 MB once sent")
    assert "costs more" in message and "Don't show this dialog again" not in message
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        dialog = dialogs.StoredSizeDialog(frame, 70_100_000, "a/one")
        assert dialog.FindWindow(dialogs.SIZE_CHAT_SETTINGS).GetLabel() == "Chat settings…"
        assert dialog.FindWindow(dialogs.SIZE_SHRINK).GetLabel() == "Shrink the stored video"
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


def test_settings_turn_the_size_check_off_and_forget_skipped_models():
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        dialog = SettingsDialog(frame, AppConfig(size_check_skipped_models=("a/one", "b/two")))
        assert dialog.reset_skipped.GetLabel() == "Ask again for the 2 models marked \"Don't show this dialog again\""
        dialog.check_size.SetValue(False)
        dialog._on_reset_skipped(None)
        assert dialog.value().check_video_size is False and dialog.value().size_check_skipped_models == ()
        assert not dialog.reset_skipped.IsEnabled()
        dialog.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


def test_filename_dialog_toggles_parts_and_resets_to_the_yt_dlp_default():
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        dialog = dialogs.FilenameDialog(frame, ("title", "id"))
        assert dialog.value() == ("title", "id")
        assert dialog.template.GetLabel() == "Template: %(title)s [%(id)s].%(ext)s"
        dialog.parts.Check(FILENAME_FIELD_NAMES.index("uploader"))
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
        dialog = dialogs.MessageReaderDialog(frame, "Model: first line\nsecond line")
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
