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

from .chat_history import chat_history_items
from .config import AppConfig, config_path_from_standard_paths, load_config, save_config
from .dialogs import NO_PROFILE, MessageReaderDialog, SettingsDialog, StartChatDialog, default_download_dir
from .openrouter import OpenRouterClient
from .prompts import system_prompt
from .session_store import VideoSession, VideoSessionStore
from .speech import announce
from .tools import ToolRunner, tool_definitions
from .youtube import download_video


def _local_user() -> str:
    # getpass also reads USERNAME, so it works on Windows, where USER is usually not set.
    try:
        return getpass.getuser() or "user"
    except Exception:
        return "user"


OWNER_ID = "local:" + _local_user()
# Earlier versions read only USER, so on Windows every chat was stored under this owner.
LEGACY_OWNER_ID = "local:" + os.environ.get("USER", "user")


class DownloadCancelled(Exception):
    """Raised from the progress hook to stop a download the user cancelled."""


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
        self.setup_button = wx.Button(panel, label="Change prompt profile and tools")
        self.delete_button = wx.Button(panel, label="Delete current chat")
        self.sessions = wx.ListBox(panel)
        left.Add(self.new_button, 0, wx.EXPAND | wx.ALL, 6)
        left.Add(self.settings_button, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        left.Add(self.update_path_button, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        left.Add(self.setup_button, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
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
        self.setup_button.Bind(wx.EVT_BUTTON, lambda _event: self.change_setup())
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
        start = self._ask_first_question()
        if start:
            self._start_chat(source, source.name, *start)

    def new_from_youtube(self) -> None:
        with wx.TextEntryDialog(self, "YouTube link:", "Start chat from YouTube", _url_from_clipboard()) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            url = dialog.GetValue().strip()
        if not url:
            return
        start = self._ask_first_question()
        if not start:
            return
        target_dir = self._download_dir()
        if target_dir is None:
            return
        downloaded = self._download_with_progress(url, target_dir)
        if downloaded:
            path, title, info = downloaded
            self._start_chat(path, title, *start, source_url=url, video_info=info)

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

    def _download_with_progress(self, url: str, target_dir: Path) -> tuple[Path, str, dict[str, Any]] | None:
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

    def _ask_first_question(self) -> tuple[str, str] | None:
        """Returns the first question and the chosen profile's name, or None if cancelled."""
        with StartChatDialog(self, self.config.profiles, self.config.profile) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return None
            question, profile = dialog.value()
        if not question:
            return None
        if (profile or NO_PROFILE) != self.config.profile:
            # Preselect the same profile next time.
            self.config = replace(self.config, profile=profile or NO_PROFILE)
            save_config(self.config_path, self.config)
        return question, profile

    def _start_chat(self, source: Path, title: str, question: str, profile: str, *, source_url: str = "",
                    video_info: dict[str, Any] | None = None) -> None:
        try:
            # Reading a large video can take a while, and one that does not fit into memory fails here.
            with wx.BusyCursor():
                self.current = self.store.create_session(
                    owner_id=OWNER_ID, source_path=source, title=title, model=self.config.model, initial_question=question,
                    # Frozen for this chat: they precede the video in every request (see VideoSession).
                    system_prompt=system_prompt(self.config.profile_text(profile)),
                    tools=tool_definitions(online_video=bool(source_url)), source_url=source_url, video_info=video_info)
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

    def change_setup(self) -> None:
        """Gives the open chat the current base prompt, a profile and the tools, e.g. for chats from before tools."""
        if not self.current:
            return
        names = [NO_PROFILE, *(name for name, _text in self.config.profiles)]
        tools = "zoom and web search" + (", high resolution and video information" if self.current.source_url else "")
        message = (f"Choose the prompt profile for this chat. It also gets the current base prompt and the tools "
                   f"({tools}). The next question sends the video uncached once, so it costs more.")
        with wx.SingleChoiceDialog(self, message, "Change prompt profile and tools", names) as dialog:
            if self.config.profile in names:
                dialog.SetSelection(names.index(self.config.profile))
            if dialog.ShowModal() != wx.ID_OK:
                return
            choice = dialog.GetStringSelection()
        self.apply_setup("" if choice == NO_PROFILE else choice)

    def apply_setup(self, profile: str) -> None:
        if not self.current:
            return
        self.store.update_setup(self.current.id, OWNER_ID, system_prompt(self.config.profile_text(profile)),
                                tool_definitions(online_video=bool(self.current.source_url)))
        self.current = self.store.get_session(self.current.id, OWNER_ID)
        self.render_current()
        self.status.SetLabel(self.status.GetLabel() + f"\nPrompt profile set to {profile or NO_PROFILE}; tools enabled.")

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

        if not initial:
            self._show_progress_item(session_id, f"You: {question}", announce_it=False)

        def on_event(kind: str, text: str) -> None:
            item = f"Tool call: {text}" if kind == "tool_call" else f"Tool result: {text}"
            wx.CallAfter(self._show_progress_item, session_id, item)

        def worker() -> None:
            try:
                session = self.store.get_session(session_id, OWNER_ID)
                messages = session.messages if initial else self.store.build_follow_up_messages(session_id, OWNER_ID, question)
                client = OpenRouterClient(api_key, base_url=self.config.base_url)
                turn = client.run_turn(session, messages, ToolRunner(session).run, on_event)
                question_entry = [] if initial else [{"role": "user", "content": question}]
                self.store.record_turn(session_id, OWNER_ID, question_entry + turn.entries)
                completion = turn.completion
                wx.CallAfter(self._completed, session_id, completion.model, completion.tools_unavailable)
            except Exception as error:
                wx.CallAfter(self._failed, str(error))

        threading.Thread(target=worker, daemon=True).start()

    def _show_progress_item(self, session_id: str, item: str, *, announce_it: bool = True) -> None:
        """Shows a step of the running request in the chat history while the model works."""
        if not self.current or self.current.id != session_id:
            return
        self.history_items.append(item)
        self.transcript.Append(item)
        if announce_it:
            announce(item, interrupt=False)

    def _completed(self, session_id: str, answered_by: str, tools_unavailable: bool = False) -> None:
        list_had_focus = wx.Window.FindFocus() is self.transcript
        self.current = self.store.get_session(session_id, OWNER_ID)
        self.render_current()
        note = " This model does not support tools, so it answered without them." if tools_unavailable else ""
        self.status.SetLabel(self.status.GetLabel() + f"\nAnswer received from {answered_by}.{note}")
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
        # Nothing of the failed turn was stored; remove its progress items from the list.
        self.render_current()
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
