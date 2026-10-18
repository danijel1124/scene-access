import wx

from sceneaccess import dialogs
from sceneaccess.config import AppConfig
from sceneaccess.dialogs import SettingsDialog
from sceneaccess.youtube import FILENAME_FIELD_NAMES


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


def test_settings_tool_round_limit_accepts_zero_and_unbounded_integers(monkeypatch):
    app = wx.App(False)
    frame = wx.Frame(None)
    try:
        with SettingsDialog(frame, AppConfig(max_tool_rounds=12)) as dialog:
            assert dialog.max_tool_rounds.GetValue() == "12"
            assert dialog.max_tool_rounds.GetName() == "Maximum tool rounds per answer (0 disables tools)"
            for value in ("0", "100000000000000000000"):
                dialog.max_tool_rounds.SetValue(value)
                assert dialog.value().max_tool_rounds == int(value)
            errors = []
            monkeypatch.setattr(wx, "MessageBox", lambda *args: errors.append(args))
            for value in ("-1", "1.5", "", "bad"):
                dialog.max_tool_rounds.SetValue(value)
                event = wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK)
                dialog._on_ok(event)
                assert not event.GetSkipped()
            assert len(errors) == 4
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
