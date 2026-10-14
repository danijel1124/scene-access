from __future__ import annotations

import getpass
import os
import sys
import threading
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any

import wx

from .config import AppConfig, config_path_from_standard_paths, load_config, save_config
from .openrouter import OpenRouterClient
from .session_store import VideoSession, VideoSessionStore
from .speech import announce
from .youtube import (DEFAULT_FILENAME_FIELDS, DEFAULT_MAX_HEIGHT, FILENAME_FIELD_NAMES, FILENAME_FIELDS,
                      MAX_HEIGHT_CHOICES, download_video, output_template)


def _local_user() -> str:
    # getpass also reads USERNAME, so it works on Windows, where USER is usually not set.
    try:
        return getpass.getuser() or "user"
    except Exception:
        return "user"


OWNER_ID = "local:" + _local_user()
# Earlier versions read only USER, so on Windows every chat was stored under this owner.
LEGACY_OWNER_ID = "local:" + os.environ.get("USER", "user")


def usage_text(usage: dict[str, Any] | None) -> str:
    if not usage:
        return "Usage: no data was recorded for this answer."
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    cached_tokens = usage.get("cached_tokens")
    cache_write_tokens = usage.get("cache_write_tokens")
    if cached_tokens is None:
        cache = "cache details were not reported by the provider"
    else:
        write = "not reported" if cache_write_tokens is None else f"{cache_write_tokens:,}"
        cache = (f"cache read (hit): {cached_tokens:,} | not from cache (miss): {max(prompt_tokens - cached_tokens, 0):,}"
                 f" | cache write: {write}")
    return f"Usage: model {usage.get('model') or 'unknown'} | prompt tokens: {prompt_tokens:,} | {cache}"


def chat_history_items(initial_question: str, history: list[dict[str, Any]]) -> list[str]:
    items = [f"You: {initial_question}"]
    for message in history:
        if message["role"] == "user":
            items.append(f"You: {message['content']}")
        else:
            items.append(f"Model: {message['content']}")
            items.append(usage_text(message.get("usage")))
    return items


class DownloadCancelled(Exception):
    """Raised from the progress hook to stop a download the user cancelled."""


def default_download_dir() -> Path:
    return Path(wx.StandardPaths.Get().GetUserDir(wx.StandardPaths.Dir_Videos)) / "scene-access"


def _url_from_clipboard() -> str:
    """Suggests a copied link as the default, so it does not have to be pasted."""
    text = ""
    if wx.TheClipboard.Open():
        try:
            data = wx.TextDataObject()
            if wx.TheClipboard.GetData(data):
                text = data.GetText().strip()
        finally:
            wx.TheClipboard.Close()
    return text if text.startswith(("https://", "http://")) and len(text.split()) == 1 else ""


def resolve_openrouter_key(config: AppConfig) -> str | None:
    return os.environ.get("OPENROUTER_API_KEY") or config.api_key or None


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


class SettingsDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, config: AppConfig):
        super().__init__(parent, title="Settings")
        # All controls are children of the dialog itself: CreateStdDialogButtonSizer parents its
        # buttons to the dialog, and wx asserts if a sizer manages windows of a different parent.
        form = wx.FlexGridSizer(6, 2, 8, 8)
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
        for label, control in (("Model", self.model), ("Base URL", self.base_url), ("API key", self.api_key),
                               ("YouTube quality", self.youtube_quality), ("Download folder", self.download_dir),
                               ("Downloaded file name", self.filename_button)):
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
        return AppConfig(self.model.GetValue().strip(), self.base_url.GetValue().strip(), self.api_key.GetValue().strip(),
                         youtube_max_height=self.heights[self.youtube_quality.GetSelection()],
                         # Keep "default" as empty, so it keeps following the Videos folder.
                         download_dir="" if download_dir == str(default_download_dir()) else download_dir,
                         filename_fields=self.filename_fields)

    def _on_filename(self, _event: wx.CommandEvent) -> None:
        with FilenameDialog(self, self.filename_fields) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.filename_fields = dialog.value()


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


class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="SceneAccess", size=(1100, 760))
        self.config_path = config_path_from_standard_paths(wx.StandardPaths.Get())
        self.config = load_config(self.config_path)
        self.store = VideoSessionStore(self.config_path.parent / "chats")
        if LEGACY_OWNER_ID != OWNER_ID:
            self.store.reassign_owner(LEGACY_OWNER_ID, OWNER_ID)
        self.current: VideoSession | None = None
        self.history_items: list[str] = []
        self.session_ids: list[str] = []
        self._build()
        self.refresh_sessions()

    def _build(self) -> None:
        panel = wx.Panel(self)
        layout = wx.BoxSizer(wx.HORIZONTAL)
        left = wx.BoxSizer(wx.VERTICAL)
        self.new_button = wx.Button(panel, label="New video chat")
        self.settings_button = wx.Button(panel, label="Settings")
        self.update_path_button = wx.Button(panel, label="Update original file path")
        self.delete_button = wx.Button(panel, label="Delete current chat")
        self.sessions = wx.ListBox(panel)
        left.Add(self.new_button, 0, wx.EXPAND | wx.ALL, 6)
        left.Add(self.settings_button, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        left.Add(self.update_path_button, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        left.Add(self.delete_button, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        left.Add(wx.StaticText(panel, label="Recent video chats"), 0, wx.LEFT | wx.TOP, 6)
        left.Add(self.sessions, 1, wx.EXPAND | wx.ALL, 6)
        right = wx.BoxSizer(wx.VERTICAL)
        self.status = wx.StaticText(panel, label="Start a new chat with a local video file.")
        # The label directly before the list gives it its accessible name for screen readers.
        history_label = wx.StaticText(panel, label="Chat history")
        self.transcript = wx.ListBox(panel)
        self.question = wx.TextCtrl(panel, style=wx.TE_MULTILINE)
        self.send_button = wx.Button(panel, label="Send question")
        right.Add(self.status, 0, wx.EXPAND | wx.ALL, 8)
        right.Add(history_label, 0, wx.LEFT | wx.RIGHT, 8)
        right.Add(self.transcript, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        right.Add(wx.StaticText(panel, label="Question"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        right.Add(self.question, 0, wx.EXPAND | wx.ALL, 8)
        right.Add(self.send_button, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        layout.Add(left, 0, wx.EXPAND | wx.ALL, 4)
        layout.Add(right, 1, wx.EXPAND | wx.ALL, 4)
        panel.SetSizer(layout)
        self.new_button.Bind(wx.EVT_BUTTON, self.on_new)
        self.settings_button.Bind(wx.EVT_BUTTON, self.on_settings)
        self.update_path_button.Bind(wx.EVT_BUTTON, self.on_update_path)
        self.delete_button.Bind(wx.EVT_BUTTON, self.on_delete)
        self.sessions.Bind(wx.EVT_LISTBOX, self.on_open)
        self.send_button.Bind(wx.EVT_BUTTON, self.on_send)
        self.transcript.Bind(wx.EVT_LISTBOX_DCLICK, lambda _event: self.open_selected_message())
        # Enter is handled before the panel's keyboard navigation can consume it.
        self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)

    def on_char_hook(self, event: wx.KeyEvent) -> None:
        if not self.handle_enter(wx.Window.FindFocus(), event.GetKeyCode(), event.ShiftDown()):
            event.Skip()

    def handle_enter(self, focused: wx.Window | None, key_code: int, shift: bool) -> bool:
        """Enter sends a question or opens a chat message; Shift+Enter keeps inserting a line break."""
        if key_code not in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) or shift:
            return False
        if focused is self.question:
            if self.send_button.IsEnabled():
                self.on_send(None)
            return True
        if focused is self.transcript:
            self.open_selected_message()
            return True
        return False

    def open_selected_message(self) -> None:
        index = self.transcript.GetSelection()
        if index == wx.NOT_FOUND or index >= len(self.history_items):
            return
        with MessageReaderDialog(self, self.history_items[index]) as dialog:
            dialog.ShowModal()
        self.transcript.SetFocus()

    def refresh_sessions(self) -> None:
        sessions = self.store.list_sessions(OWNER_ID)
        self.session_ids = [session.id for session in sessions]
        self.sessions.Set([session.title for session in sessions])

    def on_settings(self, _event: wx.CommandEvent) -> None:
        dialog = SettingsDialog(self, self.config)
        if dialog.ShowModal() == wx.ID_OK:
            candidate = dialog.value()
            if not candidate.model or not candidate.base_url.startswith(("https://", "http://")):
                wx.MessageBox("A model and a complete http(s) base URL are required.", "Invalid settings", wx.OK | wx.ICON_ERROR)
            else:
                self.config = candidate
                save_config(self.config_path, self.config)
        dialog.Destroy()

    def on_new(self, _event: wx.CommandEvent) -> None:
        menu = wx.Menu()
        # Read the IDs now: after menu.Destroy() the items are freed and GetId() returns garbage.
        local = menu.Append(wx.ID_ANY, "Start chat from local file").GetId()
        youtube = menu.Append(wx.ID_ANY, "Start chat from YouTube").GetId()
        # Returns the chosen ID once the menu has closed, so the next dialog does not open from inside it.
        chosen = self.new_button.GetPopupMenuSelectionFromUser(menu, (0, self.new_button.GetSize().height))
        menu.Destroy()
        if chosen == local:
            self.new_from_local_file()
        elif chosen == youtube:
            self.new_from_youtube()

    def new_from_local_file(self) -> None:
        with wx.FileDialog(self, "Choose a local video", wildcard="Video (*.mp4;*.webm;*.mov)|*.mp4;*.webm;*.mov", style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            source = Path(dialog.GetPath())
        question = self._ask_first_question()
        if question:
            self._start_chat(source, source.name, question)

    def new_from_youtube(self) -> None:
        with wx.TextEntryDialog(self, "YouTube link:", "Start chat from YouTube", _url_from_clipboard()) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            url = dialog.GetValue().strip()
        if not url:
            return
        question = self._ask_first_question()
        if not question:
            return
        target_dir = self._download_dir()
        if target_dir is None:
            return
        downloaded = self._download_with_progress(url, target_dir)
        if downloaded:
            self._start_chat(*downloaded, question)

    def _download_dir(self) -> Path | None:
        """Returns a usable download folder, asking for a new default when it is not available."""
        if self.config.download_dir:
            folder, available = Path(self.config.download_dir), Path(self.config.download_dir).is_dir()
            reason = f"The download folder {folder} does not exist."
        else:
            # Only the Videos folder has to exist; the scene-access subfolder is created.
            folder = default_download_dir()
            available = folder.parent.is_dir()
            reason = f"The Videos folder {folder.parent} does not exist."
        if available:
            try:
                folder.mkdir(exist_ok=True)
                return folder
            except OSError as error:
                reason = f"The download folder {folder} could not be created: {error}"
        with wx.DirDialog(self, f"{reason} Choose the default download folder:", style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return None
            folder = Path(dialog.GetPath())
        self.config = replace(self.config, download_dir=str(folder))
        save_config(self.config_path, self.config)
        return folder

    def _download_with_progress(self, url: str, target_dir: Path) -> tuple[Path, str] | None:
        max_height = self.config.youtube_max_height
        filename_fields = self.config.filename_fields
        state: dict[str, Any] = {"percent": 0, "done": False, "cancel": False, "result": None, "error": None}

        def progress(update: dict[str, Any]) -> None:
            if state["cancel"]:
                raise DownloadCancelled()
            total = update.get("total_bytes") or update.get("total_bytes_estimate")
            if update.get("status") == "downloading" and total:
                state["percent"] = int(update.get("downloaded_bytes", 0) * 100 / total)

        def worker() -> None:
            try:
                state["result"] = download_video(url, target_dir, max_height, progress, filename_fields)
            except Exception as error:
                state["error"] = error
            finally:
                state["done"] = True

        existing = set(target_dir.iterdir())
        announce("Downloading video…")
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        message = f"Downloading the video (up to {max_height}p) to {target_dir}"
        # The native Windows progress dialog runs in its own thread and may not get the focus, so a screen
        # reader does not announce it until it closes. The generic one is a normal dialog on the main thread.
        with wx.GenericProgressDialog("Downloading from YouTube", message, maximum=100, parent=self,
                                      style=wx.PD_APP_MODAL | wx.PD_CAN_ABORT | wx.PD_ELAPSED_TIME) as dialog:
            while not state["done"]:
                # Video and audio are separate downloads, so the percentage restarts once.
                keep_going, _skip = dialog.Update(min(state["percent"], 99))
                if not keep_going:
                    state["cancel"] = True
                wx.MilliSleep(100)
        thread.join()
        if state["cancel"] or state["error"] is not None:
            # Remove only the partial files this download created, never anything that was there before.
            for leftover in set(target_dir.iterdir()) - existing:
                try:
                    leftover.unlink()
                except OSError:
                    pass
        if state["cancel"]:
            self.status.SetLabel("The download was cancelled.")
            return None
        if state["error"] is not None:
            self.status.SetLabel("The video could not be downloaded.")
            wx.MessageBox(str(state["error"]), "Download failed", wx.OK | wx.ICON_ERROR)
            return None
        return state["result"]

    def _ask_first_question(self) -> str:
        with wx.TextEntryDialog(self, "First question about the video:", "Start a video chat", "Describe this video.") as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return ""
            return dialog.GetValue().strip()

    def _start_chat(self, source: Path, title: str, question: str) -> None:
        try:
            # Reading a large video can take a while, and one that does not fit into memory fails here.
            with wx.BusyCursor():
                self.current = self.store.create_session(owner_id=OWNER_ID, source_path=source, title=title, model=self.config.model, initial_question=question)
        except (OSError, MemoryError) as error:
            wx.MessageBox(f"The video could not be loaded: {error or type(error).__name__}", "Video not loaded", wx.OK | wx.ICON_ERROR)
            return
        self.refresh_sessions()
        self.render_current()
        self._send(question, initial=True)

    def on_open(self, _event: wx.CommandEvent) -> None:
        index = self.sessions.GetSelection()
        if index == wx.NOT_FOUND:
            return
        self.current = self.store.get_session(self.session_ids[index], OWNER_ID)
        self.render_current()

    def on_update_path(self, _event: wx.CommandEvent) -> None:
        if not self.current:
            return
        with wx.FileDialog(self, "Choose the original video at its new location", wildcard="Video (*.mp4;*.webm;*.mov)|*.mp4;*.webm;*.mov", style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            try:
                self.store.update_source_path(self.current.id, OWNER_ID, Path(dialog.GetPath()))
            except OSError as error:
                wx.MessageBox(f"The file path could not be updated: {error}", "Path not updated", wx.OK | wx.ICON_ERROR)
                return
        self.current = self.store.get_session(self.current.id, OWNER_ID)
        self.render_current()

    def on_delete(self, _event: wx.CommandEvent) -> None:
        if not self.current:
            return
        if wx.MessageBox("The conversation history and locally stored video data URL will be deleted.", "Delete this chat?", wx.YES_NO | wx.ICON_WARNING) != wx.YES:
            return
        self.store.delete_session(self.current.id, OWNER_ID)
        self.current = None
        self.history_items = []
        self.transcript.Clear()
        self.status.SetLabel("Chat deleted.")
        self.refresh_sessions()

    def render_current(self) -> None:
        if not self.current:
            return
        session = self.current
        context = "The local video context is stored; the original file is not required."
        source = "The original file is no longer available." if session.source_missing else "The original file is still available."
        self.status.SetLabel(f"{session.title} | {session.model}\n{context} {source}")
        items = chat_history_items(session.initial_question, session.history)
        self.history_items = items
        self.transcript.Set(items)
        # Select the newest answer rather than its usage line, so it is what gets read first.
        answers = [index for index, item in enumerate(items) if item.startswith("Model: ")]
        self.transcript.SetSelection(answers[-1] if answers else len(items) - 1)

    def on_send(self, _event: wx.CommandEvent | None) -> None:
        question = self.question.GetValue().strip()
        if not self.current or not question:
            return
        self.question.Clear()
        self._send(question, initial=not self.current.history)

    def _send(self, question: str, *, initial: bool) -> None:
        if not self.current:
            return
        api_key = resolve_openrouter_key(self.config)
        if not api_key:
            wx.MessageBox("No API key is configured. Enter one in Settings or set OPENROUTER_API_KEY.", "OpenRouter API key required", wx.OK | wx.ICON_ERROR)
            return
        session_id = self.current.id
        self.send_button.Disable()
        self.status.SetLabel("Request in progress. The video is being sent from the private local context.")
        announce("Generating answer…")

        def worker() -> None:
            try:
                session = self.store.get_session(session_id, OWNER_ID)
                messages = session.messages if initial else self.store.build_follow_up_messages(session_id, OWNER_ID, question)
                result = OpenRouterClient(api_key, base_url=self.config.base_url).complete(session, messages)
                if initial:
                    self.store.record_answer(session_id, OWNER_ID, result.answer, result.usage_record())
                else:
                    self.store.record_follow_up(session_id, OWNER_ID, question, result.answer, result.usage_record())
                wx.CallAfter(self._completed, session_id, result.model)
            except Exception as error:
                wx.CallAfter(self._failed, str(error))

        threading.Thread(target=worker, daemon=True).start()

    def _completed(self, session_id: str, answered_by: str) -> None:
        list_had_focus = wx.Window.FindFocus() is self.transcript
        self.current = self.store.get_session(session_id, OWNER_ID)
        self.render_current()
        self.status.SetLabel(self.status.GetLabel() + f"\nAnswer received from {answered_by}.")
        self.refresh_sessions()
        self.send_button.Enable()
        # render_current selected the new answer. Moving focus to the list makes the screen reader
        # read it; if the list already had focus, nothing would be read, so announce it instead.
        if list_had_focus:
            announce(self.history_items[self.transcript.GetSelection()])
        else:
            self.transcript.SetFocus()

    def _failed(self, error: str) -> None:
        self.send_button.Enable()
        self.status.SetLabel("The request failed. The chat and local video context are preserved.")
        wx.MessageBox(error, "OpenRouter error", wx.OK | wx.ICON_ERROR)


def _report_unexpected_error(error_type, error, trace) -> None:
    """The windowed exe has no console, so an error in an event handler would otherwise vanish silently."""
    text = "".join(traceback.format_exception(error_type, error, trace))
    log_path = Path(wx.StandardPaths.Get().GetUserDataDir()) / "error.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(text + "\n")
    except OSError:
        pass
    wx.MessageBox(f"An unexpected error occurred. It was also written to {log_path}.\n\n{text}",
                  "SceneAccess error", wx.OK | wx.ICON_ERROR)


def main() -> None:
    app = wx.App()
    sys.excepthook = _report_unexpected_error
    frame = MainFrame()
    frame.Show()
    app.MainLoop()
