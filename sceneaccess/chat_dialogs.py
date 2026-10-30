"""The dialogs of a chat: starting it with its advanced settings, the video size questions, Chat settings
and reading a message."""

from __future__ import annotations

from typing import Collection

import wx

from .dialogs import NO_PROFILE, AddModelDialog, youtube_quality_labels
from .models import AUTO_ROUTER
from .providers import LLAMA_CPP, OPENROUTER, PROVIDER_LABELS, PROVIDERS
from .tools import HD_FRAME, VIDEO_INFO, WEB_SEARCH, ZOOM_FRAME
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
    """The open chat's provider and own model, whether openrouter/auto may pick only hearing models, whether a
    llama.cpp chat may transcribe through OpenRouter, and shrinking its video."""

    def __init__(self, parent: wx.Window, model: str, settings_models: dict[str, str], audio_only: bool,
                 sent_size: int, shrink_target_mb: int, models: dict[str, list[str]] | None = None, *,
                 provider: str = OPENROUTER, openrouter_transcript: bool = False, openrouter_key: bool = False):
        """settings_models and models hold per provider the model in Settings and the models to find. provider
        is the chat's provider; choosing another one here moves the chat to it."""
        super().__init__(parent, title="Chat settings")
        self.settings_models, self.models = settings_models, models or {}
        self.chat_provider = self.shown_provider = provider
        self.openrouter_key = openrouter_key
        provider_label = wx.StaticText(self, label="Provider of this chat")
        self.provider = wx.Choice(self, choices=[PROVIDER_LABELS[name] for name in PROVIDERS])
        self.provider.SetSelection(PROVIDERS.index(provider))
        self.provider_note = wx.StaticText(self, label="")
        self.use_default = wx.CheckBox(self)
        self.use_default.SetValue(not model)
        model_label = wx.StaticText(self, label="Model for this chat")
        self.model = wx.TextCtrl(self, value=model or settings_models.get(provider, ""))
        self.find = wx.Button(self, label="Find model…")
        self.audio_only = wx.CheckBox(self, label="With openrouter/auto, use only models that hear the video's sound")
        self.audio_only.SetValue(audio_only)
        self.transcript = wx.CheckBox(self, label="Transcribe the speech through OpenRouter and ElevenLabs "
                                                  "(sends the video's sound; needs an OpenRouter API key)")
        self.transcript.SetValue(openrouter_transcript)
        self.shrink = wx.CheckBox(self, label=f"Shrink the stored video ({megabytes(sent_size)} once sent) to at most "
                                              f"{shrink_target_mb} MB")
        note = wx.StaticText(self, label="A new provider, a new model or a shrunk video makes the next question send "
                                         "the video uncached once, so it costs more. If the model may then be one that "
                                         "cannot hear, that question first transcribes the speech, if allowed (about "
                                         "$0.11 per hour of sound).")
        note.Wrap(580)
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        layout = wx.BoxSizer(wx.VERTICAL)
        for control in (provider_label, self.provider, self.provider_note, self.use_default, model_label, self.model,
                        self.find, self.audio_only, self.transcript, self.shrink, note):
            layout.Add(control, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(layout)
        self.SetMinSize((620, -1))
        self.provider.Bind(wx.EVT_CHOICE, lambda _event: self._on_provider())
        self.use_default.Bind(wx.EVT_CHECKBOX, lambda _event: self._update())
        self.find.Bind(wx.EVT_BUTTON, self._on_find)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self._update()

    def value(self) -> tuple[str, bool, bool]:
        """The chat's model ("" for the one in Settings), only hearing models, and whether to shrink the video."""
        model = "" if self.use_default.GetValue() else self.model.GetValue().strip()
        return model, self.audio_only.GetValue(), self.shrink.GetValue()

    def provider_value(self) -> tuple[str, bool]:
        """The chat's provider, and whether a llama.cpp chat may transcribe its speech through OpenRouter."""
        provider = PROVIDERS[self.provider.GetSelection()]
        return provider, provider == LLAMA_CPP and self.transcript.GetValue()

    def _on_provider(self) -> None:
        provider = PROVIDERS[self.provider.GetSelection()]
        if provider != self.shown_provider:
            # A model name belongs to one provider, so the new one starts with its model from Settings.
            self.shown_provider = provider
            self.use_default.SetValue(True)
            self.model.SetValue(self.settings_models.get(provider, ""))
        self._update()

    def _update(self) -> None:
        provider = self.shown_provider
        self.use_default.SetLabel(f"Use the model from Settings ({self.settings_models.get(provider, '')})")
        for control in (self.model, self.find):
            control.Enable(not self.use_default.GetValue())
        self.audio_only.Show(provider == OPENROUTER)
        self.transcript.Show(provider == LLAMA_CPP)
        self.transcript.Enable(self.openrouter_key)
        if provider == self.chat_provider:
            text = ""
        elif provider == OPENROUTER:
            text = ("From the next question on, this chat's video is sent to OpenRouter, and its sound to "
                    "ElevenLabs if the model cannot hear.")
        else:
            text = "From the next question on, this chat goes to your own llama.cpp server."
        self.provider_note.SetLabel(text)
        self.provider_note.Wrap(580)
        self.GetSizer().Fit(self)

    def _on_find(self, _event: wx.CommandEvent) -> None:
        with AddModelDialog(self, self.models.get(self.shown_provider, []), title="Choose a model for this chat",
                            allow_patterns=False) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.model.SetValue(dialog.value())
        self.model.SetFocus()

    def _on_ok(self, event: wx.CommandEvent) -> None:
        if not self.use_default.GetValue() and not self.model.GetValue().strip():
            wx.MessageBox("Enter a model or use the model from Settings.", "No model", wx.OK | wx.ICON_ERROR, self)
            return
        if self.chat_provider == LLAMA_CPP and self.shown_provider == OPENROUTER and wx.MessageBox(
                "This chat ran on your own llama.cpp server. From the next question on, its video is sent to "
                "OpenRouter and the model provider, and, if the model cannot hear, its sound to ElevenLabs for a "
                "transcript. Move the chat to OpenRouter?", "Send the video to OpenRouter?",
                wx.YES_NO | wx.ICON_WARNING, self) != wx.YES:
            return
        event.Skip()


class StartChatDialog(wx.Dialog):
    """Asks for the first question and the prompt profile of a new chat; Advanced… holds the video settings."""

    def __init__(self, parent: wx.Window, profiles: tuple[tuple[str, str], ...], selected: str, *, online: bool = False,
                 max_height: int = DEFAULT_MAX_HEIGHT, auto_shrink: bool = False,
                 shrink_target_mb: int = DEFAULT_SHRINK_TARGET_MB, question: str = "Describe this video.",
                 tools: Collection[str] | None = None, llama_cpp: bool = False, openrouter_key: bool = False,
                 transcript: bool = False):
        """tools preselects the chat's tools by key (None: all). A chat on the user's own llama.cpp server
        (llama_cpp) searches the web and transcribes through OpenRouter, which needs openrouter_key;
        transcript preselects that transcription."""
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
        tools_heading = wx.StaticText(self, label="Tools for this chat")
        search_label = ("Web search through OpenRouter (sends only search terms; needs an OpenRouter API key)"
                        if llama_cpp else "Web search (run by OpenRouter)")
        # In the order of tool_definitions; the YouTube tools exist only for online videos.
        self.tool_boxes = {key: wx.CheckBox(self, label=label) for key, label in (
            (ZOOM_FRAME, "Zoom into a frame"), (HD_FRAME, "Load in high resolution (YouTube)"),
            (VIDEO_INFO, "Read the video information (YouTube)"), (WEB_SEARCH, search_label))}
        self.available = {ZOOM_FRAME, WEB_SEARCH} | ({HD_FRAME, VIDEO_INFO} if online else set())
        if llama_cpp and not openrouter_key:
            self.available.discard(WEB_SEARCH)
        for key, box in self.tool_boxes.items():
            box.Show(key in (ZOOM_FRAME, WEB_SEARCH) or online)
            box.Enable(key in self.available)
            box.SetValue(key in self.available and (tools is None or key in tools))
            box.Bind(wx.EVT_CHECKBOX, lambda _event: self._show_toggle())
        self.toggle_tools = wx.Button(self)
        self.toggle_tools.Bind(wx.EVT_BUTTON, self._on_toggle_tools)
        self._show_toggle()
        # Not a tool: preprocessing. Off by default, so a llama.cpp chat's sound stays private unless chosen.
        self.transcript = wx.CheckBox(self, label="Transcribe the speech through OpenRouter and ElevenLabs "
                                                  "(sends the video's sound; needs an OpenRouter API key)")
        self.transcript.SetValue(transcript and openrouter_key)
        self.transcript.Enable(openrouter_key)
        self.transcript.Show(llama_cpp)
        advanced = wx.Button(self, label="Advanced…")
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        layout = wx.BoxSizer(wx.VERTICAL)
        shown_boxes = [(box, 0) for box in self.tool_boxes.values() if box.IsShown()]
        for control, proportion in ((question_label, 0), (self.question, 1), (profile_label, 0), (self.profile, 0),
                                    (text_label, 0), (self.profile_text, 1), (note, 0), (tools_heading, 0),
                                    *shown_boxes, (self.toggle_tools, 0), *([(self.transcript, 0)] if llama_cpp else [])):
            layout.Add(control, proportion, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        layout.Add(advanced, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(layout)
        self.SetSize((700, 720))
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

    def chosen_tools(self) -> tuple[str, ...]:
        """The keys of the tools chosen for this chat, see tools.tool_key."""
        return tuple(key for key, box in self.tool_boxes.items() if key in self.available and box.GetValue())

    def _show_toggle(self) -> None:
        self.toggle_tools.SetLabel("Deselect all tools" if self.chosen_tools() else "Select all tools")
        self.toggle_tools.Enable(bool(self.available))

    def _on_toggle_tools(self, _event: wx.CommandEvent | None) -> None:
        select = not self.chosen_tools()
        for key in self.available:
            self.tool_boxes[key].SetValue(select)
        self._show_toggle()

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
