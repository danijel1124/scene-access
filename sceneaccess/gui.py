from __future__ import annotations

import getpass
import os
import re
import shutil
import sys
import threading
import traceback
from dataclasses import replace
from pathlib import Path

import wx

from .chat_history import chat_history_entries, transcript_entry
from .config import AppConfig, config_path_from_standard_paths, load_config, save_config
from .chat_dialogs import (SIZE_CHAT_SETTINGS, SIZE_SEND, SIZE_SHRINK, ChatSettingsDialog, MessageReaderDialog,
                           StoredSizeDialog)
from .dialogs import NO_PROFILE, SettingsDialog
from .models import AUTO_ROUTER, ModelCatalog, routing
from .new_chat import NewChatMixin, url_from_clipboard
from .openrouter import OpenRouterClient
from .request_control import RequestControl, RequestCancelled
from .prompts import system_prompt
from .session_store import TRANSCRIPT_DONE, TRANSCRIPT_FAILED, TRANSCRIPT_NONE, TRANSCRIPT_PENDING, VideoSession, VideoSessionStore, SessionNotFound
from .speech import announce
from .tools import ToolRunner, format_time, local_video, tool_definitions
from .transcript import make_transcript, needs_transcript
from .video_size import SIZE_LIMIT, megabytes
from .youtube import fetch_video_info, search_videos

# yt-dlp's default file name ends with the video ID in brackets: "Title [jNQXAC9IVRw].mp4".
YOUTUBE_ID_IN_NAME = re.compile(r"\[([A-Za-z0-9_-]{11})\]")


def youtube_link_in_file_name(source_path: str) -> str | None:
    found = YOUTUBE_ID_IN_NAME.search(Path(source_path).name)
    return f"https://www.youtube.com/watch?v={found.group(1)}" if found else None


def is_local_file_chat(title: str) -> bool:
    # Chats from a local file are titled with the file name, downloads with the video title.
    return Path(title).suffix.lower() in (".mp4", ".webm", ".mov")


def _local_user() -> str:
    # getpass also reads USERNAME, so it works on Windows, where USER is usually not set.
    try:
        return getpass.getuser() or "user"
    except Exception:
        return "user"


OWNER_ID = "local:" + _local_user()
# Earlier versions read only USER, so on Windows every chat was stored under this owner.
LEGACY_OWNER_ID = "local:" + os.environ.get("USER", "user")


def resolve_openrouter_key(config: AppConfig) -> str | None:
    return os.environ.get("OPENROUTER_API_KEY") or config.api_key or None


class ChatWindows:
    """One independent top-level window per chat, with shared application settings."""
    def __init__(self):
        self.frames: list[MainFrame] = []
        self.config: AppConfig | None = None
        self.catalog: ModelCatalog | None = None

    def new_window(self) -> MainFrame:
        frame = MainFrame(self)
        self.frames.append(frame)
        frame.Show()
        return frame

    def open_session(self, session_id: str) -> MainFrame | None:
        for frame in self.frames:
            if frame.current and frame.current.id == session_id:
                if frame.IsIconized():
                    frame.Iconize(False)
                frame.Raise()
                return frame
        if not self.frames:
            return None
        try:
            session = self.frames[0].store.get_session(session_id, OWNER_ID)
        except SessionNotFound:
            self.refresh()
            return None
        frame = self.new_window()
        frame.current = session
        frame.render_current()
        return frame

    def refresh(self) -> None:
        for frame in self.frames:
            if not frame._closing:
                frame.refresh_sessions()


class MainFrame(NewChatMixin, wx.Frame):
    @property
    def config(self) -> AppConfig:
        return self.windows.config if self.windows and self.windows.config is not None else self._config

    @config.setter
    def config(self, value: AppConfig) -> None:
        previous = self.windows.config if self.windows else getattr(self, "_config", None)
        if previous and previous.base_url != value.base_url:
            self.catalog = ModelCatalog(value.base_url)
        self._config = value
        if self.windows:
            self.windows.config = value

    @property
    def catalog(self) -> ModelCatalog:
        return self.windows.catalog if self.windows and self.windows.catalog is not None else self._catalog

    @catalog.setter
    def catalog(self, value: ModelCatalog) -> None:
        self._catalog = value
        if self.windows:
            self.windows.catalog = value

    def __init__(self, windows: ChatWindows | None = None):
        super().__init__(None, title="SceneAccess", size=(1100, 760))
        self.windows = windows
        self._closing = False
        self._request: RequestControl | None = None
        self._pending_question = ""
        self.config_path = config_path_from_standard_paths(wx.StandardPaths.Get())
        self.config = windows.config if windows and windows.config is not None else load_config(self.config_path)
        self.owner_id = OWNER_ID
        self.store = VideoSessionStore(self.config_path.parent / "chats")
        if LEGACY_OWNER_ID != OWNER_ID:
            self.store.reassign_owner(LEGACY_OWNER_ID, OWNER_ID)
        if not windows or windows.catalog is None:
            self.catalog = ModelCatalog(self.config.base_url)
        self.current: VideoSession | None = None
        # What the Chat history shows, and what Enter opens in the reader (longer for the transcript).
        self.history_items: list[str] = []
        self.reader_texts: list[str] = []
        self.session_ids: list[str] = []
        # "Don't show this dialog again" for a model: chosen in the size dialog, kept once the answer arrives.
        self.pending_size_skip = ""
        self.size_skip_after_answer: tuple[str, str] | None = None
        # A chat whose too large stored video was sent as it is; it stops asking once the answer arrives.
        self.size_confirm_after_answer = ""
        self._build()
        self.refresh_sessions()
        self.Bind(wx.EVT_CLOSE, self.on_close)

    def on_close(self, event: wx.CloseEvent) -> None:
        self._closing = True
        if self._request:
            self._request.cancel()
        if self.windows and self in self.windows.frames:
            self.windows.frames.remove(self)
        event.Skip()

    def _start_chat(self, *args, **kwargs) -> None:
        if not self.windows:
            return super()._start_chat(*args, **kwargs)
        frame = self.windows.new_window()
        frame.pending_size_skip, self.pending_size_skip = self.pending_size_skip, ""
        NewChatMixin._start_chat(frame, *args, **kwargs)
        if frame.current is None:
            frame.Close()
        self.windows.refresh()

    def _build(self) -> None:
        panel = wx.Panel(self)
        layout = wx.BoxSizer(wx.HORIZONTAL)
        left = wx.BoxSizer(wx.VERTICAL)
        self.new_button = wx.Button(panel, label="New video chat")
        self.settings_button = wx.Button(panel, label="Settings")
        self.update_path_button = wx.Button(panel, label="Update original file path")
        self.setup_button = wx.Button(panel, label="Change prompt profile and tools")
        self.chat_settings_button = wx.Button(panel, label="Chat settings")
        self.delete_button = wx.Button(panel, label="Delete current chat")
        self.sessions = wx.ListBox(panel)
        left.Add(self.new_button, 0, wx.EXPAND | wx.ALL, 6)
        left.Add(self.settings_button, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        left.Add(self.update_path_button, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        left.Add(self.setup_button, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        left.Add(self.chat_settings_button, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        left.Add(self.delete_button, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        left.Add(wx.StaticText(panel, label="Recent video chats"), 0, wx.LEFT | wx.TOP, 6)
        left.Add(self.sessions, 1, wx.EXPAND | wx.ALL, 6)
        right = wx.BoxSizer(wx.VERTICAL)
        self.status = wx.StaticText(panel, label="Start a new chat with a local video file.")
        # The label directly before the list gives it its accessible name for screen readers.
        history_label = wx.StaticText(panel, label="Chat history")
        self.chat_list = wx.ListBox(panel)
        self.question = wx.TextCtrl(panel, style=wx.TE_MULTILINE)
        self.send_button = wx.Button(panel, label="Send question")
        self.pause_button = wx.Button(panel, label="Pause request")
        self.pause_button.Hide()
        right.Add(self.status, 0, wx.EXPAND | wx.ALL, 8)
        right.Add(history_label, 0, wx.LEFT | wx.RIGHT, 8)
        right.Add(self.chat_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        right.Add(wx.StaticText(panel, label="Question"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        right.Add(self.question, 0, wx.EXPAND | wx.ALL, 8)
        right.Add(self.send_button, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        right.Add(self.pause_button, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        layout.Add(left, 0, wx.EXPAND | wx.ALL, 4)
        layout.Add(right, 1, wx.EXPAND | wx.ALL, 4)
        panel.SetSizer(layout)
        self.new_button.Bind(wx.EVT_BUTTON, self.on_new)
        self.settings_button.Bind(wx.EVT_BUTTON, self.on_settings)
        self.update_path_button.Bind(wx.EVT_BUTTON, self.on_update_path)
        self.setup_button.Bind(wx.EVT_BUTTON, lambda _event: self.change_setup())
        self.chat_settings_button.Bind(wx.EVT_BUTTON, lambda _event: self.change_chat_settings())
        self.delete_button.Bind(wx.EVT_BUTTON, self.on_delete)
        self.sessions.Bind(wx.EVT_LISTBOX, self.on_open)
        self.send_button.Bind(wx.EVT_BUTTON, self.on_send)
        self.pause_button.Bind(wx.EVT_BUTTON, self.on_pause)
        self.chat_list.Bind(wx.EVT_LISTBOX_DCLICK, lambda _event: self.open_selected_message())
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
        if focused is self.chat_list:
            self.open_selected_message()
            return True
        return False

    def open_selected_message(self) -> None:
        index = self.chat_list.GetSelection()
        if index == wx.NOT_FOUND or index >= len(self.reader_texts):
            return
        with MessageReaderDialog(self, self.reader_texts[index]) as dialog:
            dialog.ShowModal()
        self.chat_list.SetFocus()

    def refresh_sessions(self) -> None:
        sessions = self.store.list_sessions(OWNER_ID)
        self.session_ids = [session.id for session in sessions]
        self.sessions.Set([session.title for session in sessions])

    def on_settings(self, _event: wx.CommandEvent) -> None:
        dialog = SettingsDialog(self, self.config, self.catalog)
        if dialog.ShowModal() == wx.ID_OK:
            candidate = dialog.value()
            if not candidate.model or not candidate.base_url.startswith(("https://", "http://")):
                wx.MessageBox("A model and a complete http(s) base URL are required.", "Invalid settings", wx.OK | wx.ICON_ERROR)
            else:
                self.config = candidate
                save_config(self.config_path, self.config)
        dialog.Destroy()

    def on_open(self, _event: wx.CommandEvent) -> None:
        index = self.sessions.GetSelection()
        if index == wx.NOT_FOUND:
            return
        if self.windows:
            self.windows.open_session(self.session_ids[index])
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
        if not self.current.source_url and not self._find_online_source():
            return
        names = [NO_PROFILE, *(name for name, _text in self.config.profiles)]
        tools = "zoom and web search" + (", high resolution and video information" if self.current.source_url else "")
        message = (f"Choose the prompt profile for this chat. It also gets the current base prompt and the tools "
                   f"({tools}), and a transcript if the model cannot hear. The next question sends the video "
                   "uncached once, so it costs more.")
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
                                tool_definitions(online_video=bool(self.current.source_url)),
                                # The chat keeps its own choice from Chat settings.
                                audio_only=self.current.audio_only)
        self.current = self.store.get_session(self.current.id, OWNER_ID)
        self.render_current()
        self.status.SetLabel(self.status.GetLabel() + f"\nPrompt profile set to {profile or NO_PROFILE}; tools enabled.")

    def chat_model(self, session: VideoSession) -> str:
        """The model a chat uses: its own, or the one in Settings if it has none ("default")."""
        return session.model or self.config.model

    def change_chat_settings(self) -> None:
        if not self.current:
            return
        session = self.current
        with wx.BusyCursor():
            models = self.catalog.model_ids()
        with ChatSettingsDialog(self, session.model, self.config.model, session.audio_only, session.sent_video_size,
                                self.config.shrink_target_mb, models) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            model, audio_only, shrink = dialog.value()
        if (model, audio_only) != (session.model, session.audio_only):
            self.store.update_chat_settings(session.id, OWNER_ID, model, audio_only)
        if shrink:
            self.shrink_stored_video()
        self.current = self.store.get_session(session.id, OWNER_ID)
        self.render_current()

    def shrink_stored_video(self) -> bool:
        """Replaces the open chat's stored video by a shrunk copy, made from the original file if it is still
        there. Returns False if cancelled or failed."""
        with local_video(self.current) as source:
            shrunk = self._shrink_with_progress(source)
        if not shrunk:
            return False
        try:
            self.store.replace_video(self.current.id, OWNER_ID, shrunk)
        finally:
            shutil.rmtree(shrunk.parent, ignore_errors=True)
        self.current = self.store.get_session(self.current.id, OWNER_ID)
        self.status.SetLabel(self.status.GetLabel() + f"\nThe stored video was shrunk to "
                                                      f"{megabytes(self.current.sent_video_size)} once sent.")
        return True

    def _ask_about_stored_size(self) -> bool:
        """Before a question in a chat whose stored video is too large: asks what to do. True to send now."""
        session = self.current
        model = self.chat_model(session)
        if (not self.config.check_video_size or session.size_confirmed or session.sent_video_size <= SIZE_LIMIT
                or (model != AUTO_ROUTER and model in self.config.size_check_skipped_models)):
            return True
        with StoredSizeDialog(self, session.sent_video_size, model) as dialog:
            choice = dialog.ShowModal()
            dont_ask = dialog.dont_ask.IsShown() and dialog.dont_ask.GetValue()
        if choice == SIZE_SHRINK:
            return self.shrink_stored_video()
        if choice == SIZE_CHAT_SETTINGS:
            self.change_chat_settings()
            return False
        if choice == SIZE_SEND:
            # Kept once the answer arrives, like for a new chat; then this chat stops asking too.
            self.size_skip_after_answer = (session.id, model) if dont_ask else None
            self.size_confirm_after_answer = session.id
            return True
        return False

    def _find_online_source(self) -> bool:
        """Asks which YouTube video an older chat came from, as chats did not store the link at first.
        Returns False if the user cancelled."""
        session = self.current
        link = youtube_link_in_file_name(session.source_path)
        if not link and is_local_file_chat(session.title):
            return True
        choices: list[tuple[str, str]] = []
        if link:
            choices.append((f"The video ID in the file name: {link}", link))
        announce("Searching YouTube…")
        try:
            results = self._with_youtube_sign_in(lambda cookies: search_videos(session.title, cookies=cookies))
        except Exception as error:
            results = []
            wx.MessageBox(f"YouTube could not be searched: {error}", "Search failed", wx.OK | wx.ICON_WARNING)
        for result in results:
            length = f", {format_time(result['duration'])}" if result.get("duration") else ""
            choices.append((f"{result['title']}, by {result['channel'] or 'unknown'}{length}", result["url"]))
        choices += [("Enter a link…", "enter"), ("Not a YouTube video", "none")]
        message = f"Which YouTube video is \"{session.title}\"? With the link, the model can also load moments in high resolution and read the video information."
        with wx.SingleChoiceDialog(self, message, "Online source of this chat", [label for label, _url in choices]) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return False
            url = choices[dialog.GetSelection()][1]
        if url == "none":
            return True
        if url == "enter":
            with wx.TextEntryDialog(self, "YouTube link:", "Online source of this chat", url_from_clipboard()) as dialog:
                if dialog.ShowModal() != wx.ID_OK:
                    return False
                url = dialog.GetValue().strip()
            if not url:
                return True
        announce("Loading the video information…")
        try:
            info = self._with_youtube_sign_in(lambda cookies: fetch_video_info(url, cookies=cookies))
        except Exception as error:
            wx.MessageBox(f"The video information could not be loaded: {error}", "Link not used", wx.OK | wx.ICON_WARNING)
            return True
        self.store.update_source(session.id, OWNER_ID, url, info)
        self.current = self.store.get_session(session.id, OWNER_ID)
        return True

    def on_delete(self, _event: wx.CommandEvent) -> None:
        if not self.current:
            return
        if wx.MessageBox("The conversation history and locally stored video data URL will be deleted.", "Delete this chat?", wx.YES_NO | wx.ICON_WARNING) != wx.YES:
            return
        self.store.delete_session(self.current.id, OWNER_ID)
        self.current = None
        self.history_items = []
        self.reader_texts = []
        self.chat_list.Clear()
        self.status.SetLabel("Chat deleted.")
        self.SetTitle("SceneAccess")
        self.refresh_sessions()
        if self.windows:
            self.windows.refresh()

    def render_current(self) -> None:
        if not self.current:
            return
        session = self.current
        self.SetTitle(f"{session.title} - SceneAccess")
        context = "The local video context is stored; the original file is not required."
        source = "The original file is no longer available." if session.source_missing else "The original file is still available."
        model = session.model or f"{self.config.model} (from Settings)"
        self.status.SetLabel(f"{session.title} | {model}\n{context} {source}")
        entries = chat_history_entries(session.initial_question, session.history, session.transcript_state, session.transcript)
        items = [shown for shown, _full in entries]
        self.history_items = items
        self.reader_texts = [full for _shown, full in entries]
        self.chat_list.Set(items)
        # Select the newest answer rather than its usage line, so it is what gets read first.
        answers = [index for index, item in enumerate(items) if item.startswith("Model: ")]
        self.chat_list.SetSelection(answers[-1] if answers else len(items) - 1)

    def on_send(self, _event: wx.CommandEvent | None) -> None:
        if self._request:
            self._request.cancel()
            self.send_button.Disable()
            self.pause_button.Disable()
            self.status.SetLabel("Cancelling request… Already submitted provider work may still be charged.")
            return
        question = self.question.GetValue().strip()
        if not self.current or not question:
            return
        # The question stays in the field if the user cancels or first changes the chat settings.
        if not self._ask_about_stored_size():
            return
        self.question.Clear()
        self._send(question, initial=not self.current.history)

    def _send(self, question: str, *, initial: bool) -> None:
        if not self.current or self._request:
            return
        api_key = resolve_openrouter_key(self.config)
        if not api_key:
            self.question.SetValue(question)
            wx.MessageBox("No API key is configured. Enter one in Settings or set OPENROUTER_API_KEY.", "OpenRouter API key required", wx.OK | wx.ICON_ERROR)
            return
        session_id = self.current.id
        if initial:
            self.store.update_initial_question(session_id, OWNER_ID, question)
        control = self._request = RequestControl()
        config = self.config
        catalog = self.catalog
        self._pending_question = question
        self._set_request_buttons(True)
        self.status.SetLabel("Request in progress. The video is being sent from the private local context.")
        announce("Generating answer…")

        if not initial:
            self._show_progress_item(session_id, f"You: {question}", announce_it=False)

        def on_event(kind: str, text: str) -> None:
            if kind == "paused":
                wx.CallAfter(self._request_callback, control, self._paused, text)
                return
            item = f"Tool call: {text}" if kind == "tool_call" else f"Tool result: {text}"
            wx.CallAfter(self._request_callback, control, self._show_progress_item, session_id, item)

        def worker() -> None:
            try:
                client = OpenRouterClient(api_key, base_url=config.base_url, control=control)
                # A chat on "default" asks the model that is in Settings now.
                session = self.store.get_session(session_id, OWNER_ID)
                follows_settings = not session.model
                session = replace(session, model=session.model or config.model)
                if follows_settings and session.transcript_state == TRANSCRIPT_NONE and needs_transcript(session, catalog):
                    # Settings now name a model that cannot hear, so this chat needs a transcript after all.
                    session = replace(session, transcript_state=TRANSCRIPT_PENDING)
                if session.transcript_state == TRANSCRIPT_PENDING:
                    self._prepare_transcript(session, client, catalog)
                    session = self.store.get_session(session_id, OWNER_ID)
                    session = replace(session, model=session.model or config.model)
                messages = session.messages if initial else self.store.build_follow_up_messages(session_id, OWNER_ID, question)
                # Raises if this chat may only go to hearing models but the model list is unavailable.
                options = routing(session.model, session.audio_only, catalog, config.ignored_providers,
                                  config.excluded_models)
                runner = ToolRunner(session, config.youtube_cookies, control=control)
                turn = client.run_turn(session, messages, runner.run, on_event, options,
                                       max_tool_rounds=config.max_tool_rounds)
                control.check_cancelled()
                question_entry = [] if initial else [{"role": "user", "content": question}]
                self.store.record_turn(session_id, OWNER_ID, question_entry + turn.entries)
                completion = turn.completion
                wx.CallAfter(self._request_callback, control, self._completed, session_id, completion.model, completion.tools_unavailable)
            except RequestCancelled:
                wx.CallAfter(self._request_callback, control, self._cancelled)
            except Exception as error:
                wx.CallAfter(self._request_callback, control, self._failed, str(error))

        threading.Thread(target=worker, daemon=True).start()

    def _request_callback(self, control: RequestControl, function, *args) -> None:
        if not self._closing and self._request is control:
            function(*args)

    def _set_request_buttons(self, running: bool) -> None:
        self.send_button.SetLabel("Cancel request" if running else "Send question")
        self.send_button.Enable()
        self.pause_button.SetLabel("Pause request")
        self.pause_button.Enable()
        self.pause_button.Show(running)
        for button in (self.delete_button, self.setup_button, self.chat_settings_button, self.update_path_button):
            button.Enable(not running)
        self.Layout()

    def on_pause(self, _event: wx.CommandEvent | None) -> None:
        if not self._request:
            return
        if self._request.pause_requested:
            self._request.resume()
            self.pause_button.SetLabel("Pause request")
            self.status.SetLabel("Request resumed.")
            announce("Request resumed.")
        else:
            self._request.pause()
            self.pause_button.SetLabel("Resume request")
            self.status.SetLabel("The request will pause after the current round, including its local tools.")
            announce("The request will pause after the current round.")

    def _paused(self, text: str) -> None:
        if self._request and self._request.pause_requested:
            self.pause_button.SetLabel("Resume request")
            self.status.SetLabel(text)
            announce(text)

    def _finish_request(self, *, restore_question: bool = False) -> None:
        self._request = None
        if restore_question and not self.question.GetValue().strip():
            self.question.SetValue(self._pending_question)
        self._pending_question = ""
        self._set_request_buttons(False)

    def _cancelled(self) -> None:
        self.size_skip_after_answer = None
        self.size_confirm_after_answer = ""
        self._finish_request(restore_question=True)
        self.render_current()
        self.status.SetLabel("Request cancelled. Completed chat history is preserved; provider charges may still apply.")
        announce("Request cancelled.")

    def _prepare_transcript(self, session: VideoSession, client: OpenRouterClient,
                            catalog: ModelCatalog | None = None) -> None:
        """Runs in the request thread: transcribes the sound once if the chat's model cannot hear it.
        A failure never stops the answer; the chat remembers it, so it is not paid for on every question."""
        if not needs_transcript(session, catalog or self.catalog):
            self.store.update_transcript(session.id, OWNER_ID, TRANSCRIPT_NONE)
            return
        wx.CallAfter(self._show_progress_item, session.id, "Transcribing the video's speech for a model that cannot hear it…")
        try:
            text = make_transcript(session, client)
            self.store.update_transcript(session.id, OWNER_ID, TRANSCRIPT_DONE, text)
            shown, full = transcript_entry(TRANSCRIPT_DONE, text)
        except RequestCancelled:
            raise
        except Exception as error:
            self.store.update_transcript(session.id, OWNER_ID, TRANSCRIPT_FAILED)
            shown, full = transcript_entry(TRANSCRIPT_FAILED, "")
            shown = full = f"{shown} Reason: {error}"
        wx.CallAfter(self._show_progress_item, session.id, shown, full=full)

    def _show_progress_item(self, session_id: str, item: str, *, announce_it: bool = True, full: str | None = None) -> None:
        """Shows a step of the running request in the chat history while the model works."""
        if self._closing or not self.current or self.current.id != session_id:
            return
        self.history_items.append(item)
        self.reader_texts.append(item if full is None else full)
        self.chat_list.Append(item)
        if announce_it:
            announce(item, interrupt=False)

    def _completed(self, session_id: str, answered_by: str, tools_unavailable: bool = False) -> None:
        self._finish_request()
        if not self.current or self.current.id != session_id:
            self.refresh_sessions()
            return
        if self.size_skip_after_answer and self.size_skip_after_answer[0] == session_id:
            # Sending the large video worked, so do not ask about its size again for this model.
            model = self.size_skip_after_answer[1]
            self.size_skip_after_answer = None
            if model not in self.config.size_check_skipped_models:
                self.config = replace(self.config, size_check_skipped_models=(*self.config.size_check_skipped_models, model))
                save_config(self.config_path, self.config)
        if self.size_confirm_after_answer == session_id:
            self.size_confirm_after_answer = ""
            self.store.confirm_size(session_id, OWNER_ID)
        list_had_focus = wx.Window.FindFocus() is self.chat_list
        self.current = self.store.get_session(session_id, OWNER_ID)
        self.render_current()
        note = " This model does not support tools, so it answered without them." if tools_unavailable else ""
        self.status.SetLabel(self.status.GetLabel() + f"\nAnswer received from {answered_by}.{note}")
        self.refresh_sessions()
        self.send_button.Enable()
        # render_current selected the new answer. Moving focus to the list makes the screen reader
        # read it; if the list already had focus, nothing would be read, so announce it instead.
        if not self.IsActive():
            return  # A background answer must not steal focus from another chat or application.
        if list_had_focus:
            announce(self.history_items[self.chat_list.GetSelection()])
        else:
            self.chat_list.SetFocus()

    def _failed(self, error: str) -> None:
        self._finish_request(restore_question=True)
        self.size_skip_after_answer = None
        self.size_confirm_after_answer = ""
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
    windows = ChatWindows()
    windows.new_window()
    app.MainLoop()
