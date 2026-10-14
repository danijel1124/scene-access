"""The dialogs of a chat: starting it with its advanced settings, the video size questions, Chat settings
and reading a message."""

from __future__ import annotations

import wx

from .dialogs import NO_PROFILE, AddModelDialog, youtube_quality_labels
from .models import AUTO_ROUTER
from .video_size import DEFAULT_SHRINK_TARGET_MB, SIZE_LIMIT, base64_size, megabytes
from .youtube import DEFAULT_MAX_HEIGHT, MAX_HEIGHT_CHOICES


class AdvancedDialog(wx.Dialog):
    """The YouTube resolution and automatic shrinking of too large videos; kept as defaults for the next chat."""

    def __init__(self, parent: wx.Window, online: bool, max_height: int, auto_shrink: bool, shrink_target_mb: int):
        super().__init__(parent, title="Advanced")
        layout = wx.BoxSizer(wx.VERTICAL)
        self.heights = sorted({*MAX_HEIGHT_CHOICES, max_height})
        self.youtube_quality = wx.Choice(self, choices=youtube_quality_labels(self.heights))
        self.youtube_quality.SetSelection(self.heights.index(max_height))
        if online:
            layout.Add(wx.StaticText(self, label="YouTube quality"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
            layout.Add(self.youtube_quality, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        else:
            self.youtube_quality.Hide()
        self.auto_shrink = wx.CheckBox(self, label="Shrink videos that are too large automatically with ffmpeg, without asking")
        self.auto_shrink.SetValue(auto_shrink)
        target_label = wx.StaticText(self, label="Largest size to send, in MB after Base64 encoding")
        self.shrink_target = wx.SpinCtrl(self, min=5, max=2000, initial=shrink_target_mb)
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        for control in (self.auto_shrink, target_label, self.shrink_target):
            layout.Add(control, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(layout)
        layout.Fit(self)

    def value(self) -> tuple[int, bool, int]:
        """The YouTube height, whether to shrink automatically, and the size to shrink to in MB."""
        return self.heights[self.youtube_quality.GetSelection()], self.auto_shrink.GetValue(), self.shrink_target.GetValue()


# What the size dialog returns besides wx.ID_CANCEL.
SIZE_SHRINK, SIZE_LOWER, SIZE_SEND, SIZE_BACK = (wx.ID_HIGHEST + 101, wx.ID_HIGHEST + 102, wx.ID_HIGHEST + 103,
                                                 wx.ID_HIGHEST + 104)


def size_message(file_size: int, model: str, online: bool) -> str:
    sent = base64_size(file_size)
    lower = (", or YouTube can be asked for a lower resolution, one step at a time until the video fits"
             if online else "")
    skip = (f"If you send it and it works, check \"Don't show this dialog again\" to skip this question for {model}.\n\n"
            if model != AUTO_ROUTER else "")
    choices = ("Shrink the video, request a lower resolution, or send it as it is?" if online
               else "Shrink the video or send it as it is?")
    return (f"Some models accept videos only up to about {megabytes(SIZE_LIMIT)}. This video is {megabytes(file_size)}; "
            f"the Base64 encoding used for sending adds about {megabytes(sent - file_size)}, so "
            f"{megabytes(sent)} would be sent.\n\n"
            f"The video can be shrunk automatically{lower}. You can also send it as it is, but then it may not work.\n\n"
            f"{skip}"
            "Settings can turn this size check off completely; then a video that is too large for the model causes "
            "an error. In the Start a video chat dialog, Advanced… changes the YouTube quality or sets up automatic "
            "shrinking with ffmpeg to a size of your choice.\n\n"
            f"{choices} Back returns to the Start a video chat dialog for these settings.")


class SizeDialog(wx.Dialog):
    """Asks what to do with a video too large for some providers. ShowModal returns one of the SIZE_ IDs."""

    def __init__(self, parent: wx.Window, file_size: int, model: str, online: bool, can_lower: bool = True):
        super().__init__(parent, title="The video is large", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        label = wx.StaticText(self, label="Message")
        self.message = wx.TextCtrl(self, value=size_message(file_size, model, online),
                                   style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 260))
        # A setting per model: with openrouter/auto, the next answer may come from another model.
        self.dont_ask = wx.CheckBox(self, label="Don't show this dialog again")
        self.dont_ask.Show(model != AUTO_ROUTER)
        row = wx.BoxSizer(wx.HORIZONTAL)
        actions = [(SIZE_SHRINK, "Shrink the video"), (SIZE_LOWER, "Request a lower resolution"),
                   (SIZE_SEND, "Send as it is"), (SIZE_BACK, "Back to Start a video chat"), (wx.ID_CANCEL, "Cancel")]
        for action, text in actions:
            if action == SIZE_LOWER and not online:
                continue
            button = wx.Button(self, action, text)
            button.Enable(action != SIZE_LOWER or can_lower)
            button.Bind(wx.EVT_BUTTON, lambda _event, chosen=action: self.EndModal(chosen))
            row.Add(button, 0, wx.RIGHT, 8)
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        layout.Add(self.message, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        layout.Add(self.dont_ask, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        layout.Add(row, 0, wx.ALL, 12)
        self.SetSizer(layout)
        self.SetSize((760, 520))
        self.message.SetFocus()


SIZE_CHAT_SETTINGS = wx.ID_HIGHEST + 105


def stored_size_message(sent_size: int, model: str) -> str:
    skip = (f"If you send it and it works, check \"Don't show this dialog again\" to skip this question for {model}.\n\n"
            if model != AUTO_ROUTER else "")
    return (f"This chat's stored video is {megabytes(sent_size)} once sent, but some models accept videos only up "
            f"to about {megabytes(SIZE_LIMIT)}, so this question may fail with \"Invalid content\".\n\n"
            "Shrink the stored video: SceneAccess makes a smaller copy for this chat with ffmpeg. The next question "
            "then sends the video uncached once, so it costs more.\n\n"
            "Chat settings: choose another model for this chat, for example turn off \"only models that hear\", so "
            "that openrouter/auto may pick a model that only sees the picture and gets a transcript of the speech "
            "(about $0.11 per hour of sound, made once).\n\n"
            f"Send as it is: it may not work.\n\n{skip}"
            "Settings can turn this size check off completely.")


class StoredSizeDialog(wx.Dialog):
    """Asks what to do before a question in a chat whose stored video is too large for some providers."""

    def __init__(self, parent: wx.Window, sent_size: int, model: str):
        super().__init__(parent, title="The chat's video is large", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        label = wx.StaticText(self, label="Message")
        self.message = wx.TextCtrl(self, value=stored_size_message(sent_size, model),
                                   style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 260))
        self.dont_ask = wx.CheckBox(self, label="Don't show this dialog again")
        self.dont_ask.Show(model != AUTO_ROUTER)
        row = wx.BoxSizer(wx.HORIZONTAL)
        for action, text in ((SIZE_SHRINK, "Shrink the stored video"), (SIZE_CHAT_SETTINGS, "Chat settings…"),
                             (SIZE_SEND, "Send as it is"), (wx.ID_CANCEL, "Cancel")):
            button = wx.Button(self, action, text)
            button.Bind(wx.EVT_BUTTON, lambda _event, chosen=action: self.EndModal(chosen))
            row.Add(button, 0, wx.RIGHT, 8)
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        layout.Add(self.message, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        layout.Add(self.dont_ask, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        layout.Add(row, 0, wx.ALL, 12)
        self.SetSizer(layout)
        self.SetSize((760, 520))
        self.message.SetFocus()


class ChatSettingsDialog(wx.Dialog):
    """The open chat's own model, whether openrouter/auto may pick only hearing models, and shrinking its video."""

    def __init__(self, parent: wx.Window, model: str, settings_model: str, audio_only: bool, sent_size: int,
                 shrink_target_mb: int, models: list[str] | None = None):
        super().__init__(parent, title="Chat settings")
        self.models = models or []
        self.use_default = wx.CheckBox(self, label=f"Use the model from Settings ({settings_model})")
        self.use_default.SetValue(not model)
        model_label = wx.StaticText(self, label="Model for this chat")
        self.model = wx.TextCtrl(self, value=model or settings_model)
        self.find = wx.Button(self, label="Find model…")
        self.audio_only = wx.CheckBox(self, label="With openrouter/auto, use only models that hear the video's sound")
        self.audio_only.SetValue(audio_only)
        self.shrink = wx.CheckBox(self, label=f"Shrink the stored video ({megabytes(sent_size)} once sent) to at most "
                                              f"{shrink_target_mb} MB")
        note = wx.StaticText(self, label="A new model or a shrunk video makes the next question send the video "
                                         "uncached once, so it costs more. If the model may then be one that cannot "
                                         "hear, that question first transcribes the speech (about $0.11 per hour of sound).")
        note.Wrap(580)
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        layout = wx.BoxSizer(wx.VERTICAL)
        for control in (self.use_default, model_label, self.model, self.find, self.audio_only, self.shrink, note):
            layout.Add(control, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(layout)
        self.SetMinSize((620, -1))
        layout.Fit(self)
        self.use_default.Bind(wx.EVT_CHECKBOX, lambda _event: self._update())
        self.find.Bind(wx.EVT_BUTTON, self._on_find)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self._update()

    def value(self) -> tuple[str, bool, bool]:
        """The chat's model ("" for the one in Settings), only hearing models, and whether to shrink the video."""
        model = "" if self.use_default.GetValue() else self.model.GetValue().strip()
        return model, self.audio_only.GetValue(), self.shrink.GetValue()

    def _update(self) -> None:
        for control in (self.model, self.find):
            control.Enable(not self.use_default.GetValue())

    def _on_find(self, _event: wx.CommandEvent) -> None:
        with AddModelDialog(self, self.models, title="Choose a model for this chat", allow_patterns=False) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.model.SetValue(dialog.value())
        self.model.SetFocus()

    def _on_ok(self, event: wx.CommandEvent) -> None:
        if not self.use_default.GetValue() and not self.model.GetValue().strip():
            wx.MessageBox("Enter a model or use the model from Settings.", "No model", wx.OK | wx.ICON_ERROR, self)
            return
        event.Skip()


class StartChatDialog(wx.Dialog):
    """Asks for the first question and the prompt profile of a new chat; Advanced… holds the video settings."""

    def __init__(self, parent: wx.Window, profiles: tuple[tuple[str, str], ...], selected: str, *, online: bool = False,
                 max_height: int = DEFAULT_MAX_HEIGHT, auto_shrink: bool = False,
                 shrink_target_mb: int = DEFAULT_SHRINK_TARGET_MB, question: str = "Describe this video."):
        super().__init__(parent, title="Start a video chat", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.profiles = profiles
        self.online = online
        self.max_height, self.auto_shrink, self.shrink_target_mb = max_height, auto_shrink, shrink_target_mb
        question_label = wx.StaticText(self, label="First question about the video (Enter starts, Shift+Enter adds a line)")
        self.question = wx.TextCtrl(self, value=question, style=wx.TE_MULTILINE, size=(-1, 110))
        profile_label = wx.StaticText(self, label="Prompt profile")
        self.choices = [NO_PROFILE, *(name for name, _text in profiles)]
        self.profile = wx.Choice(self, choices=self.choices)
        self.profile.SetSelection(self.choices.index(selected) if selected in self.choices else 0)
        text_label = wx.StaticText(self, label="Profile text")
        self.profile_text = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 110))
        note = wx.StaticText(self, label="The profile stays fixed for this chat, so the video can be cached.")
        advanced = wx.Button(self, label="Advanced…")
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        layout = wx.BoxSizer(wx.VERTICAL)
        for control, proportion in ((question_label, 0), (self.question, 1), (profile_label, 0), (self.profile, 0),
                                    (text_label, 0), (self.profile_text, 1), (note, 0)):
            layout.Add(control, proportion, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        layout.Add(advanced, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(layout)
        self.SetSize((700, 560))
        advanced.Bind(wx.EVT_BUTTON, self._on_advanced)
        self.profile.Bind(wx.EVT_CHOICE, lambda _event: self._show_profile())
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self._show_profile()
        self.question.SetFocus()
        self.question.SelectAll()

    def value(self) -> tuple[str, str]:
        """The question and the chosen profile's name, empty for no profile."""
        name = self.choices[self.profile.GetSelection()]
        return self.question.GetValue().strip(), "" if name == NO_PROFILE else name

    def _on_advanced(self, _event: wx.CommandEvent) -> None:
        with AdvancedDialog(self, self.online, self.max_height, self.auto_shrink, self.shrink_target_mb) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.max_height, self.auto_shrink, self.shrink_target_mb = dialog.value()

    def _show_profile(self) -> None:
        _question, name = self.value()
        self.profile_text.ChangeValue(dict(self.profiles).get(name, "") if name else "Only the base prompt is used.")

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        if (wx.Window.FindFocus() is self.question and event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
                and not event.ShiftDown()):
            self.EndModal(wx.ID_OK)
            return
        event.Skip()


class MessageReaderDialog(wx.Dialog):
    """Shows one chat message in a read-only text field, so it can be read line by line."""

    def __init__(self, parent: wx.Window, text: str):
        super().__init__(parent, title="Message", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        label = wx.StaticText(self, label="Message text")
        self.text = wx.TextCtrl(self, value=text, style=wx.TE_MULTILINE | wx.TE_READONLY)
        buttons = self.CreateStdDialogButtonSizer(wx.CLOSE)
        self.SetEscapeId(wx.ID_CLOSE)
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        layout.Add(self.text, 1, wx.EXPAND | wx.ALL, 12)
        layout.Add(buttons, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self.SetSizer(layout)
        self.SetSize((800, 600))
        self.text.SetFocus()
