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
        config = AppConfig(profiles=(("Mine", "My text"),), profile="Mine")
        dialog = SettingsDialog(frame, config)
        assert dialog.value().profiles == (("Mine", "My text"),)
        assert dialog.value().profile == "Mine"
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
