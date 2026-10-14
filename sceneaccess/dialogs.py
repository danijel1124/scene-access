"""The dialogs of SceneAccess: settings and their sub-dialogs, starting a chat and reading a message."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import wx

from .config import AppConfig
from .models import ModelCatalog, api_url
from .prompts import BASE_SYSTEM_PROMPT, DEFAULT_PROFILES
from .youtube import (DEFAULT_FILENAME_FIELDS, DEFAULT_MAX_HEIGHT, FILENAME_FIELD_NAMES, FILENAME_FIELDS,
                      MAX_HEIGHT_CHOICES, output_template)

NO_PROFILE = "None (base prompt only)"


def default_download_dir() -> Path:
    return Path(wx.StandardPaths.Get().GetUserDir(wx.StandardPaths.Dir_Videos)) / "scene-access"


def cookies_description(cookies: str) -> str:
    if not cookies:
        return "Not signed in"
    if cookies == "firefox":
        return "Firefox"
    return f"Cookies file {cookies}"


def ask_youtube_sign_in(parent: wx.Window, reason: str = "") -> str | None:
    """Asks where the user is signed in to YouTube. Returns the cookies setting, or None if cancelled."""
    options = [("Firefox", "firefox"),
               # Chrome and Edge encrypt their cookies so that yt-dlp cannot read them on Windows.
               ("Chrome, Edge or another browser: choose an exported cookies.txt file…", "file"),
               ("Not signed in: use no cookies", "")]
    message = (f"{reason}\n\n" if reason else "") + ("Where are you signed in to YouTube? SceneAccess then uses that "
                                                     "browser's YouTube cookies for downloads and searches.")
    with wx.SingleChoiceDialog(parent, message, "YouTube sign-in", [label for label, _value in options]) as dialog:
        if dialog.ShowModal() != wx.ID_OK:
            return None
        value = options[dialog.GetSelection()][1]
    if value != "file":
        return value
    with wx.FileDialog(parent, "Choose the cookies.txt file exported from your browser",
                       wildcard="Cookies file (*.txt)|*.txt", style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
        return dialog.GetPath() if dialog.ShowModal() == wx.ID_OK else None


class FilenameDialog(wx.Dialog):
    """Chooses the yt-dlp fields a downloaded video's file name is built from."""

    def __init__(self, parent: wx.Window, fields: tuple[str, ...]):
        super().__init__(parent, title="File name of downloaded videos", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        label = wx.StaticText(self, label="File name parts (Space toggles a part)")
        self.parts = wx.CheckListBox(self, choices=[f"{name}: {description}" for name, description in FILENAME_FIELDS])
        self.parts.SetCheckedItems([index for index, name in enumerate(FILENAME_FIELD_NAMES) if name in fields])
        self.template = wx.StaticText(self)
        reset = wx.Button(self, label="Reset to yt-dlp default")
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        layout.Add(self.parts, 1, wx.EXPAND | wx.ALL, 12)
        layout.Add(self.template, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)
        layout.Add(reset, 0, wx.ALL, 12)
        layout.Add(buttons, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self.SetSizer(layout)
        self.SetSize((700, 600))
        self.parts.Bind(wx.EVT_CHECKLISTBOX, lambda _event: self._show_template())
        reset.Bind(wx.EVT_BUTTON, self._on_reset)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self._show_template()

    def value(self) -> tuple[str, ...]:
        return tuple(FILENAME_FIELD_NAMES[index] for index in self.parts.GetCheckedItems())

    def _show_template(self) -> None:
        self.template.SetLabel("Template: " + output_template(self.value()) if self.value() else "Template: choose at least one part")

    def _on_reset(self, _event: wx.CommandEvent) -> None:
        self.parts.SetCheckedItems([FILENAME_FIELD_NAMES.index(name) for name in DEFAULT_FILENAME_FIELDS])
        self._show_template()

    def _on_ok(self, event: wx.CommandEvent) -> None:
        if not self.value():
            wx.MessageBox("Choose at least one file name part.", "No file name part", wx.OK | wx.ICON_ERROR)
            return
        event.Skip()


class ProfilesDialog(wx.Dialog):
    """Edits the prompt profiles. The base prompt is fixed and shown for reference."""

    def __init__(self, parent: wx.Window, profiles: tuple[tuple[str, str], ...]):
        super().__init__(parent, title="Prompt profiles", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.profiles = [[name, text] for name, text in profiles]
        base_label = wx.StaticText(self, label="Base prompt, always sent first (not editable)")
        base = wx.TextCtrl(self, value=BASE_SYSTEM_PROMPT, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 140))
        list_label = wx.StaticText(self, label="Profiles")
        self.names = wx.ListBox(self, choices=[name for name, _text in self.profiles])
        name_label = wx.StaticText(self, label="Profile name")
        self.name = wx.TextCtrl(self)
        text_label = wx.StaticText(self, label="Profile text, added after the base prompt")
        self.text = wx.TextCtrl(self, style=wx.TE_MULTILINE, size=(-1, 160))
        add = wx.Button(self, label="Add profile")
        self.delete = wx.Button(self, label="Delete profile")
        reset = wx.Button(self, label="Reset to the default profiles")
        row = wx.BoxSizer(wx.HORIZONTAL)
        for button in (add, self.delete, reset):
            row.Add(button, 0, wx.RIGHT, 8)
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        layout = wx.BoxSizer(wx.VERTICAL)
        for control, proportion in ((base_label, 0), (base, 0), (list_label, 0), (self.names, 1), (name_label, 0),
                                    (self.name, 0), (text_label, 0), (self.text, 1), (row, 0)):
            layout.Add(control, proportion, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(layout)
        self.SetSize((760, 760))
        self.names.Bind(wx.EVT_LISTBOX, lambda _event: self._show_selected())
        self.name.Bind(wx.EVT_TEXT, self._on_name)
        self.text.Bind(wx.EVT_TEXT, self._on_text)
        add.Bind(wx.EVT_BUTTON, self._on_add)
        self.delete.Bind(wx.EVT_BUTTON, self._on_delete)
        reset.Bind(wx.EVT_BUTTON, self._on_reset)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        if self.profiles:
            self.names.SetSelection(0)
        self._show_selected()

    def value(self) -> tuple[tuple[str, str], ...]:
        return tuple((name.strip(), text) for name, text in self.profiles)

    def _selected(self) -> int:
        return self.names.GetSelection()

    def _show_selected(self) -> None:
        index = self._selected()
        has_selection = index != wx.NOT_FOUND
        for control in (self.name, self.text, self.delete):
            control.Enable(has_selection)
        # ChangeValue does not send EVT_TEXT, so showing a profile does not count as editing it.
        self.name.ChangeValue(self.profiles[index][0] if has_selection else "")
        self.text.ChangeValue(self.profiles[index][1] if has_selection else "")

    def _on_name(self, _event: wx.CommandEvent) -> None:
        index = self._selected()
        if index != wx.NOT_FOUND:
            self.profiles[index][0] = self.name.GetValue()
            self.names.SetString(index, self.name.GetValue())

    def _on_text(self, _event: wx.CommandEvent) -> None:
        index = self._selected()
        if index != wx.NOT_FOUND:
            self.profiles[index][1] = self.text.GetValue()

    def _on_add(self, _event: wx.CommandEvent) -> None:
        names = {name for name, _text in self.profiles}
        number = 1
        while f"New profile {number}" in names:
            number += 1
        self.profiles.append([f"New profile {number}", ""])
        self.names.Append(self.profiles[-1][0])
        self.names.SetSelection(len(self.profiles) - 1)
        self._show_selected()
        self.name.SetFocus()
        self.name.SelectAll()

    def _on_delete(self, _event: wx.CommandEvent) -> None:
        index = self._selected()
        if index == wx.NOT_FOUND:
            return
        del self.profiles[index]
        self.names.Delete(index)
        if self.profiles:
            self.names.SetSelection(min(index, len(self.profiles) - 1))
        self._show_selected()
        self.names.SetFocus()

    def _on_reset(self, _event: wx.CommandEvent) -> None:
        if wx.MessageBox("Replace all profiles with the default profiles?", "Reset profiles", wx.YES_NO | wx.ICON_QUESTION, self) != wx.YES:
            return
        self.profiles = [[name, text] for name, text in DEFAULT_PROFILES]
        self.names.Set([name for name, _text in self.profiles])
        self.names.SetSelection(0)
        self._show_selected()

    def _on_ok(self, event: wx.CommandEvent) -> None:
        names = [name.strip() for name, _text in self.profiles]
        if any(not name for name in names) or len(set(names)) != len(names):
            wx.MessageBox("Every profile needs a name, and the names must be different.", "Profile names", wx.OK | wx.ICON_ERROR, self)
            return
        event.Skip()


class AddModelDialog(wx.Dialog):
    """Finds a model to exclude by typing part of its ID; a pattern with * such as "google/*" can be added too."""

    def __init__(self, parent: wx.Window, models: list[str]):
        super().__init__(parent, title="Exclude a model", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.models = models
        self.entries: list[str] = []
        filter_label = wx.StaticText(self, label="Part of the model ID, or a pattern such as google/* (Enter adds the selected model)")
        self.filter = wx.TextCtrl(self)
        self.list_label = wx.StaticText(self)
        self.matches = wx.ListBox(self)
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        layout = wx.BoxSizer(wx.VERTICAL)
        for control, proportion in ((filter_label, 0), (self.filter, 0), (self.list_label, 0), (self.matches, 1)):
            layout.Add(control, proportion, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(layout)
        self.SetSize((640, 560))
        self.filter.Bind(wx.EVT_TEXT, lambda _event: self._show_matches())
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self._show_matches()
        self.filter.SetFocus()

    def value(self) -> str:
        index = self.matches.GetSelection()
        return self.entries[index] if index != wx.NOT_FOUND else ""

    def _show_matches(self) -> None:
        text = self.filter.GetValue().strip()
        found = [model for model in self.models if text.lower() in model.lower()] if text else []
        self.entries = ([text] if "*" in text else []) + found
        self.matches.Set([f"Pattern: {entry}" if "*" in entry else entry for entry in self.entries])
        if self.entries:
            self.matches.SetSelection(0)
        self.list_label.SetLabel(f"Matching models ({len(found)})" if text else "Matching models (type to search)")

    def _on_ok(self, event: wx.CommandEvent) -> None:
        if not self.value():
            wx.MessageBox("Type part of a model ID and choose a model from the list.", "No model chosen", wx.OK | wx.ICON_ERROR, self)
            return
        event.Skip()


class ExclusionsDialog(wx.Dialog):
    """Chooses providers that never answer and models that openrouter/auto never picks."""

    def __init__(self, parent: wx.Window, providers: list[tuple[str, str]], ignored: tuple[str, ...],
                 models: list[str], excluded: tuple[str, ...]):
        super().__init__(parent, title="Excluded providers and models", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        known = {slug for _name, slug in providers}
        # Excluded providers that OpenRouter no longer lists, or all of them if the list did not load.
        self.providers = [*providers, *((slug, slug) for slug in ignored if slug not in known)]
        self.models = models
        self.excluded = list(excluded)
        provider_label = wx.StaticText(self, label="Excluded providers, for every model (Space toggles a provider)")
        self.provider_list = wx.CheckListBox(self, choices=[name if name == slug else f"{name} ({slug})"
                                                            for name, slug in self.providers])
        self.provider_list.SetCheckedItems([index for index, (_name, slug) in enumerate(self.providers) if slug in ignored])
        model_label = wx.StaticText(self, label="Excluded models, used with openrouter/auto")
        self.model_list = wx.ListBox(self, choices=self.excluded)
        add = wx.Button(self, label="Add model…")
        self.remove = wx.Button(self, label="Remove model")
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(add, 0, wx.RIGHT, 8)
        row.Add(self.remove, 0)
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        layout = wx.BoxSizer(wx.VERTICAL)
        controls = [(provider_label, 0), (self.provider_list, 2), (model_label, 0), (self.model_list, 1), (row, 0)]
        if not providers:
            controls.insert(0, (wx.StaticText(self, label="OpenRouter's provider list could not be loaded, so only "
                                                           "the providers excluded so far are shown."), 0))
        for control, proportion in controls:
            layout.Add(control, proportion, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(layout)
        self.SetSize((700, 720))
        add.Bind(wx.EVT_BUTTON, self._on_add)
        self.remove.Bind(wx.EVT_BUTTON, self._on_remove)
        self._update_remove()

    def value(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """The excluded provider slugs and the excluded models."""
        return tuple(self.providers[index][1] for index in self.provider_list.GetCheckedItems()), tuple(self.excluded)

    def add_model(self, model: str) -> None:
        if model not in self.excluded:
            self.excluded.append(model)
            self.model_list.Append(model)
        self.model_list.SetSelection(self.excluded.index(model))
        self._update_remove()

    def _update_remove(self) -> None:
        self.remove.Enable(bool(self.excluded))

    def _on_add(self, _event: wx.CommandEvent) -> None:
        with AddModelDialog(self, self.models) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.add_model(dialog.value())
        self.model_list.SetFocus()

    def _on_remove(self, _event: wx.CommandEvent) -> None:
        index = self.model_list.GetSelection()
        if index == wx.NOT_FOUND:
            return
        del self.excluded[index]
        self.model_list.Delete(index)
        if self.excluded:
            self.model_list.SetSelection(min(index, len(self.excluded) - 1))
        self._update_remove()
        self.model_list.SetFocus()


class StartChatDialog(wx.Dialog):
    """Asks for the first question and the prompt profile of a new chat."""

    def __init__(self, parent: wx.Window, profiles: tuple[tuple[str, str], ...], selected: str):
        super().__init__(parent, title="Start a video chat", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.profiles = profiles
        question_label = wx.StaticText(self, label="First question about the video (Enter starts, Shift+Enter adds a line)")
        self.question = wx.TextCtrl(self, value="Describe this video.", style=wx.TE_MULTILINE, size=(-1, 110))
        profile_label = wx.StaticText(self, label="Prompt profile")
        self.choices = [NO_PROFILE, *(name for name, _text in profiles)]
        self.profile = wx.Choice(self, choices=self.choices)
        self.profile.SetSelection(self.choices.index(selected) if selected in self.choices else 0)
        text_label = wx.StaticText(self, label="Profile text")
        self.profile_text = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 110))
        note = wx.StaticText(self, label="The profile stays fixed for this chat, so the video can be cached.")
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        layout = wx.BoxSizer(wx.VERTICAL)
        for control, proportion in ((question_label, 0), (self.question, 1), (profile_label, 0), (self.profile, 0),
                                    (text_label, 0), (self.profile_text, 1), (note, 0)):
            layout.Add(control, proportion, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(layout)
        self.SetSize((700, 520))
        self.profile.Bind(wx.EVT_CHOICE, lambda _event: self._show_profile())
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self._show_profile()
        self.question.SetFocus()
        self.question.SelectAll()

    def value(self) -> tuple[str, str]:
        """The question and the chosen profile's name, empty for no profile."""
        name = self.choices[self.profile.GetSelection()]
        return self.question.GetValue().strip(), "" if name == NO_PROFILE else name

    def _show_profile(self) -> None:
        _question, name = self.value()
        self.profile_text.ChangeValue(dict(self.profiles).get(name, "") if name else "Only the base prompt is used.")

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        if (wx.Window.FindFocus() is self.question and event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
                and not event.ShiftDown()):
            self.EndModal(wx.ID_OK)
            return
        event.Skip()


def exclusions_description(providers: tuple[str, ...], models: tuple[str, ...]) -> str:
    def count(number: int, word: str) -> str:
        return f"{number or 'no'} {word}{'' if number == 1 else 's'}"
    text = f"{count(len(providers), 'provider')} and {count(len(models), 'model')} excluded"
    return text[0].upper() + text[1:]


class SettingsDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, config: AppConfig, catalog: ModelCatalog | None = None):
        super().__init__(parent, title="Settings")
        # All controls are children of the dialog itself: CreateStdDialogButtonSizer parents its
        # buttons to the dialog, and wx asserts if a sizer manages windows of a different parent.
        self.config = config
        self.catalog = catalog
        form = wx.FlexGridSizer(10, 2, 8, 8)
        form.AddGrowableCol(1, 1)
        self.model = wx.TextCtrl(self, value=config.model)
        self.base_url = wx.TextCtrl(self, value=config.base_url)
        self.api_key = wx.TextCtrl(self, value=config.api_key, style=wx.TE_PASSWORD)
        self.heights = sorted({*MAX_HEIGHT_CHOICES, config.youtube_max_height})
        self.youtube_quality = wx.Choice(self, choices=[
            f"Up to {height}p" + (" (recommended)" if height == DEFAULT_MAX_HEIGHT else "") for height in self.heights
        ])
        self.youtube_quality.SetSelection(self.heights.index(config.youtube_max_height))
        self.download_dir = wx.DirPickerCtrl(self, path=config.download_dir or str(default_download_dir()),
                                             style=wx.DIRP_USE_TEXTCTRL | wx.DIRP_DIR_MUST_EXIST)
        self.filename_fields = config.filename_fields
        self.filename_button = wx.Button(self, label="File name…")
        self.filename_button.Bind(wx.EVT_BUTTON, self._on_filename)
        self.profiles = config.profiles
        self.profiles_button = wx.Button(self, label="Prompt profiles…")
        self.profiles_button.Bind(wx.EVT_BUTTON, self._on_profiles)
        # Off: openrouter/auto may pick a model that cannot hear, which then gets a transcript instead.
        self.audio_only = wx.CheckBox(self, label="With openrouter/auto, use only models that hear the video's sound")
        self.audio_only.SetValue(config.audio_only_auto)
        self.youtube_cookies = config.youtube_cookies
        self.cookies_button = wx.Button(self)
        self._show_cookies()
        self.cookies_button.Bind(wx.EVT_BUTTON, self._on_cookies)
        self.ignored_providers, self.excluded_models = config.ignored_providers, config.excluded_models
        self.exclusions_button = wx.Button(self)
        self._show_exclusions()
        self.exclusions_button.Bind(wx.EVT_BUTTON, self._on_exclusions)
        for label, control in (("Model", self.model), ("Base URL", self.base_url), ("API key", self.api_key),
                               ("YouTube quality", self.youtube_quality), ("Download folder", self.download_dir),
                               ("Downloaded file name", self.filename_button), ("YouTube sign-in", self.cookies_button),
                               ("Prompt profiles", self.profiles_button), ("Sound", self.audio_only),
                               ("Providers and models", self.exclusions_button)):
            form.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            form.Add(control, 1, wx.EXPAND)
        help_text = wx.StaticText(self, label="The key is stored in the local application configuration. OPENROUTER_API_KEY takes precedence.")
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(form, 1, wx.EXPAND | wx.ALL, 12)
        layout.Add(help_text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(layout)
        self.SetMinSize((620, -1))
        layout.Fit(self)

    def value(self) -> AppConfig:
        download_dir = self.download_dir.GetPath().strip()
        # replace keeps settings this dialog does not show, such as the profile chosen last.
        return replace(self.config, model=self.model.GetValue().strip(), base_url=self.base_url.GetValue().strip(),
                       api_key=self.api_key.GetValue().strip(),
                       youtube_max_height=self.heights[self.youtube_quality.GetSelection()],
                       # Keep "default" as empty, so it keeps following the Videos folder.
                       download_dir="" if download_dir == str(default_download_dir()) else download_dir,
                       filename_fields=self.filename_fields, profiles=self.profiles,
                       audio_only_auto=self.audio_only.GetValue(), youtube_cookies=self.youtube_cookies,
                       ignored_providers=self.ignored_providers, excluded_models=self.excluded_models)

    def _show_exclusions(self) -> None:
        self.exclusions_button.SetLabel(f"{exclusions_description(self.ignored_providers, self.excluded_models)} (change…)")
        self.Layout()

    def _on_exclusions(self, _event: wx.CommandEvent) -> None:
        # The base URL may have been changed in this dialog, so ask the API it now names.
        base_url, catalog = self.base_url.GetValue().strip(), self.catalog
        if catalog is None or catalog.url != api_url(base_url, "models"):
            catalog = ModelCatalog(base_url)
        with wx.BusyCursor():
            providers, models = catalog.providers(), catalog.model_ids()
        with ExclusionsDialog(self, providers, self.ignored_providers, models, self.excluded_models) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.ignored_providers, self.excluded_models = dialog.value()
                self._show_exclusions()

    def _show_cookies(self) -> None:
        self.cookies_button.SetLabel(f"{cookies_description(self.youtube_cookies)} (change…)")
        self.Layout()

    def _on_cookies(self, _event: wx.CommandEvent) -> None:
        choice = ask_youtube_sign_in(self)
        if choice is not None:
            self.youtube_cookies = choice
            self._show_cookies()

    def _on_filename(self, _event: wx.CommandEvent) -> None:
        with FilenameDialog(self, self.filename_fields) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.filename_fields = dialog.value()

    def _on_profiles(self, _event: wx.CommandEvent) -> None:
        with ProfilesDialog(self, self.profiles) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.profiles = dialog.value()


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
