from __future__ import annotations

import ctypes
import re
import shutil
import sys
import threading
import traceback
from dataclasses import replace
from pathlib import Path

import wx

from ..pipeline import answering
from .app_window import AppWindow
from ..settings.config import resolve_openrouter_key
from ..storage.session_store import LEGACY_OWNER_ID, OWNER_ID, open_store
from .chat_history import ANSWER, NOTE, chat_history_rows, thinking_item, transcript_entry
from .chat_list import ChatListFrame
from ..settings.config import AppConfig, config_path_from_standard_paths, load_config, save_config
from .chat_dialogs import (SIZE_CHAT_SETTINGS, SIZE_SEND, SIZE_SHRINK, ChatSettingsDialog, MessageReaderDialog,
                           StoredSizeDialog)
from .dialogs import NO_PROFILE
from ..llm.models import AUTO_ROUTER, ModelCatalog, llama_cpp_catalog
from .new_chat import NewChatMixin, url_from_clipboard
from ..llm.client import ChatClient
from ..providers import LLAMA_CPP, OPENROUTER, OPENROUTER_URL, PROVIDER_LABELS
from ..request_control import RequestControl, RequestCancelled
from ..prompts import current_system_prompt, system_prompt
from ..storage.session_store import (EXCLUDED, TRANSCRIPT_DONE, TRANSCRIPT_FAILED, TRANSCRIPT_NONE, TRANSCRIPT_PENDING,
                                     VideoSession, VideoSessionStore, SessionNotFound, round_bounds)
from . import speech
from .single_instance import SingleInstance
from ..timecodes import format_time
from ..llm.tool_calls import WEB_SEARCH
from ..pipeline.tools import function_tool_names, tool_definitions, tool_key, tools_for_provider
from ..media.video import local_video
from ..media.transcript import make_transcript
from ..media.video_size import SIZE_LIMIT, megabytes
from ..media.youtube import fetch_video_info, search_videos

# Windows list box messages for the item the keyboard is on, which a multiple-selection list keeps apart.
LB_SETCARETINDEX, LB_GETCARETINDEX = 0x019E, 0x019F
# yt-dlp's default file name ends with the video ID in brackets: "Title [jNQXAC9IVRw].mp4".
YOUTUBE_ID_IN_NAME = re.compile(r"\[([A-Za-z0-9_-]{11})\]")
# The items that grow while an answer is streamed.
LIVE_LABELS = {"thinking": "Model is thinking", "text": "Model is writing"}


def youtube_link_in_file_name(source_path: str) -> str | None:
    found = YOUTUBE_ID_IN_NAME.search(Path(source_path).name)
    return f"https://www.youtube.com/watch?v={found.group(1)}" if found else None


def is_local_file_chat(title: str) -> bool:
    # Chats from a local file are titled with the file name, downloads with the video title.
    return Path(title).suffix.lower() in (".mp4", ".webm", ".mov")


class ChatWindows:
    """The app's windows: the chat list (the main window) and one window per open chat. They share the
    settings, OpenRouter's model catalogue and the stored chats."""
    def __init__(self):
        self.main: ChatListFrame | None = None
        self.frames: list[ChatFrame] = []
        self.config: AppConfig | None = None
        self.catalog: ModelCatalog | None = None
        self.config_path: Path | None = None
        self.store: VideoSessionStore | None = None
        self.owner_id = OWNER_ID
        self.last_active: wx.Frame | None = None
        self.requests: dict[str, RequestControl] = {}

    def prepare(self) -> None:
        """Loads what all windows share, once, before the first window."""
        if self.config_path is None:
            self.config_path = config_path_from_standard_paths(wx.StandardPaths.Get())
        if self.config is None:
            self.config = load_config(self.config_path)
        if self.store is None:
            self.store = open_store(self.config_path, OWNER_ID, LEGACY_OWNER_ID)
        if self.catalog is None:
            # OpenRouter's models: which hear, routing and exclusions. llama.cpp chats ask their server.
            self.catalog = ModelCatalog(OPENROUTER_URL)

    def all_windows(self) -> list[wx.Frame]:
        return ([self.main] if self.main and not self.main._closing else []) + self.frames

    def show_list(self) -> ChatListFrame:
        self.prepare()
        if self.main is None:
            self.main = ChatListFrame(self)
        self.main.Show()
        self.main.sessions.SetFocus()
        return self.main

    def new_chat_window(self) -> ChatFrame:
        """A hidden window for a chat about to start; chat_started shows it once the chat exists."""
        self.prepare()
        frame = ChatFrame(self)
        self.frames.append(frame)
        return frame

    def chat_started(self, frame: ChatFrame) -> None:
        if frame.current is None:
            # Never shown, so the screen reader does not announce a window that disappears at once.
            frame.Close()
            self.refresh()
        else:
            if self.main and not self.main._closing:
                self.main.refresh_sessions(select=frame.current.id)
            self._show(frame)

    def open_session(self, session_id: str) -> ChatFrame | None:
        for frame in self.frames:
            if frame.current and frame.current.id == session_id:
                self._show(frame)
                return frame
        self.prepare()
        try:
            session = self.store.get_session(session_id, OWNER_ID)
        except SessionNotFound:
            self.refresh()
            return None
        frame = self.new_chat_window()
        frame.current = session
        frame.render_current()
        self._show(frame)
        return frame

    def _show(self, frame: ChatFrame) -> None:
        if frame.IsIconized():
            frame.Iconize(False)
        frame.Show()
        frame.Raise()
        # Raising alone leaves the screen reader's focus where it was; the history has the newest answer selected.
        frame.chat_list.SetFocus()

    def close_session(self, session_id: str) -> None:
        for frame in list(self.frames):
            if frame.current and frame.current.id == session_id:
                frame.Close()

    def close_all_chats(self) -> None:
        for frame in list(self.frames):
            frame.Close()

    def chat_closed(self, frame: ChatFrame) -> None:
        if frame in self.frames:
            self.frames.remove(frame)
        if self.main and not self.main._closing and frame.IsShown():
            wx.CallAfter(self._focus_list)

    def _focus_list(self) -> None:
        if self.main and not self.main._closing:
            self.main.Raise()
            self.main.sessions.SetFocus()

    def settings_changed(self) -> None:
        """Chats on "default" show the model from Settings, so they are shown again."""
        for frame in self.frames:
            if not frame._closing and frame.current:
                frame.render_current()

    def request_finished(self, session_id: str, control: RequestControl) -> None:
        if self.requests.get(session_id) is not control:
            return
        self.requests.pop(session_id)
        for frame in self.frames:
            if (not frame._closing and not frame._request and frame.current
                    and frame.current.id == session_id):
                try:
                    frame.current = frame.store.get_session(session_id, OWNER_ID)
                except SessionNotFound:
                    continue
                frame.render_current()

    def activate(self) -> None:
        """Brings back the window used last, e.g. when SceneAccess is started a second time. A chat window
        still starting is hidden and not brought back."""
        windows = [window for window in self.all_windows() if window is self.main or window.IsShown()]
        frame = self.last_active if self.last_active in windows else (windows[-1] if windows else None)
        if frame is not None:
            if frame.IsIconized():
                frame.Iconize(False)
            frame.Raise()
            frame.SetFocus()

    def refresh(self) -> None:
        if self.main and not self.main._closing:
            self.main.refresh_sessions()


class ChatFrame(AppWindow, NewChatMixin, wx.Frame):
    """The window of one chat: its history, the question field and the chat's own settings. The chat list
    (chat_list.ChatListFrame) starts and opens chats."""

    def __init__(self, windows: ChatWindows | None = None):
        super().__init__(None, title="SceneAccess", size=(900, 760))
        self.windows = windows
        self._closing = False
        self._request: RequestControl | None = None
        self._pending_question = ""
        if windows:
            windows.prepare()
            self.config_path, self.store = windows.config_path, windows.store
        else:
            # A window on its own, e.g. in a test: it keeps its own settings, catalogue and stored chats.
            self.config_path = config_path_from_standard_paths(wx.StandardPaths.Get())
            self.config = load_config(self.config_path)
            self.store = open_store(self.config_path, OWNER_ID, LEGACY_OWNER_ID)
            self.catalog = ModelCatalog(OPENROUTER_URL)
        self.owner_id = OWNER_ID
        self.current: VideoSession | None = None
        # What the Chat history shows, and what Enter opens in the reader (longer for the transcript).
        self.history_items: list[str] = []
        self.reader_texts: list[str] = []
        self.history_rows: list[tuple[int | None, str]] = []
        # "Don't show this dialog again" for a model: chosen in the size dialog, kept once the answer arrives.
        self.pending_size_skip = ""
        self.size_skip_after_answer: tuple[str, str] | None = None
        # A chat whose too large stored video was sent as it is; it stops asking once the answer arrives.
        self.size_confirm_after_answer = ""
        self._build()
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_ACTIVATE, self.on_activate)

    def on_activate(self, event: wx.ActivateEvent) -> None:
        if event.GetActive() and self.windows:
            self.windows.last_active = self
        event.Skip()

    def on_close(self, event: wx.CloseEvent) -> None:
        self._closing = True
        if self._request:
            self._request.cancel()
        if self.windows:
            self.windows.chat_closed(self)
        event.Skip()

    def refresh_sessions(self) -> None:
        """The chat list shows titles and order; a chat window has no list of its own."""
        if self.windows:
            self.windows.refresh()

    def _build(self) -> None:
        panel = wx.Panel(self)
        layout = wx.BoxSizer(wx.VERTICAL)
        self.status = wx.StaticText(panel, label="")
        # The label directly before the list gives it its accessible name for screen readers.
        history_label = wx.StaticText(panel, label="Chat history")
        # Several items can be selected (Shift+arrows, Ctrl+Space) to copy, hide or delete them together.
        self.chat_list = wx.ListBox(panel, style=wx.LB_EXTENDED)
        self.question = wx.TextCtrl(panel, style=wx.TE_MULTILINE)
        self.send_button = wx.Button(panel, label="Send question")
        # Shown only while the first question has no stored answer, e.g. after it failed: sends it again unchanged.
        # Typing something else instead would replace the first question, as "try again" did once.
        self.resend_button = wx.Button(panel, label="Send first question again")
        self.resend_button.Hide()
        self.pause_button = wx.Button(panel, label="Pause request")
        self.pause_button.Hide()
        self.chat_settings_button = wx.Button(panel, label="Chat settings")
        # Shown only while the chat's frozen system prompt is an earlier version of the current one. Created
        # right after Chat settings, as the order of creation is the Tab order.
        self.prompt_button = wx.Button(panel, label="Update system prompt")
        self.prompt_button.Hide()
        self.setup_button = wx.Button(panel, label="Change prompt profile and tools")
        self.update_path_button = wx.Button(panel, label="Update original file path")
        self.transcript_button = wx.Button(panel, label="Add a transcript of the speech")
        layout.Add(self.status, 0, wx.EXPAND | wx.ALL, 8)
        layout.Add(history_label, 0, wx.LEFT | wx.RIGHT, 8)
        layout.Add(self.chat_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        layout.Add(wx.StaticText(panel, label="Question"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        layout.Add(self.question, 0, wx.EXPAND | wx.ALL, 8)
        requests = wx.BoxSizer(wx.HORIZONTAL)
        requests.Add(self.send_button, 0, wx.RIGHT, 8)
        requests.Add(self.resend_button, 0, wx.RIGHT, 8)
        requests.Add(self.pause_button, 0)
        layout.Add(requests, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        chat = wx.BoxSizer(wx.HORIZONTAL)
        for button in (self.chat_settings_button, self.prompt_button, self.setup_button, self.transcript_button,
                       self.update_path_button):
            chat.Add(button, 0, wx.RIGHT, 8)
        layout.Add(chat, 0, wx.ALL, 8)
        panel.SetSizer(layout)
        self.update_path_button.Bind(wx.EVT_BUTTON, self.on_update_path)
        self.prompt_button.Bind(wx.EVT_BUTTON, lambda _event: self.update_system_prompt())
        self.setup_button.Bind(wx.EVT_BUTTON, lambda _event: self.change_setup())
        self.chat_settings_button.Bind(wx.EVT_BUTTON, lambda _event: self.change_chat_settings())
        self.transcript_button.Bind(wx.EVT_BUTTON, lambda _event: self.add_transcript())
        self.send_button.Bind(wx.EVT_BUTTON, self.on_send)
        self.resend_button.Bind(wx.EVT_BUTTON, self.on_resend)
        self.pause_button.Bind(wx.EVT_BUTTON, self.on_pause)
        self.chat_list.Bind(wx.EVT_LISTBOX_DCLICK, lambda _event: self.open_selected_message())
        self.chat_list.Bind(wx.EVT_CONTEXT_MENU, self.on_history_menu)
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

    def history_caret(self) -> int:
        """The history item the keyboard is on. The list can select several items, so this is not always the
        selection, and it is what the screen reader reads."""
        if sys.platform == "win32":
            caret = ctypes.windll.user32.SendMessageW(self.chat_list.GetHandle(), LB_GETCARETINDEX, 0, 0)
            if 0 <= caret < self.chat_list.GetCount():
                return caret
        selections = self.chat_list.GetSelections()
        return selections[0] if selections else wx.NOT_FOUND

    def select_history_item(self, position: int) -> None:
        """Selects only this item and puts the keyboard on it, so the screen reader reads it."""
        for selected in self.chat_list.GetSelections():
            self.chat_list.Deselect(selected)
        if 0 <= position < self.chat_list.GetCount():
            self.chat_list.SetSelection(position)
            if sys.platform == "win32":
                ctypes.windll.user32.SendMessageW(self.chat_list.GetHandle(), LB_SETCARETINDEX, position, 0)

    def open_selected_message(self) -> None:
        index = self.history_caret()
        if index == wx.NOT_FOUND or index >= len(self.reader_texts):
            return
        with MessageReaderDialog(self, self.reader_texts[index]) as dialog:
            dialog.ShowModal()
        self.chat_list.SetFocus()

    def on_update_path(self, _event: wx.CommandEvent) -> None:
        if not self.current or self._chat_busy():
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
        if not self.current or self._chat_busy():
            return
        if not self.current.source_url and not self._find_online_source():
            return
        names = [NO_PROFILE, *(name for name, _text in self.config.profiles)]
        search = " and web search" if self.current.provider == OPENROUTER or self._local_web_search() else ""
        tools = "zoom" + search + (", high resolution and video information" if self.current.source_url else "")
        message = (f"Choose the prompt profile for this chat. It also gets the current base prompt and the tools "
                   f"({tools}), and keeps a transcript it has. The next question sends the video uncached once, so "
                   "it costs more.")
        with wx.SingleChoiceDialog(self, message, "Change prompt profile and tools", names) as dialog:
            if self.config.profile in names:
                dialog.SetSelection(names.index(self.config.profile))
            if dialog.ShowModal() != wx.ID_OK:
                return
            choice = dialog.GetStringSelection()
        self.apply_setup("" if choice == NO_PROFILE else choice)

    def apply_setup(self, profile: str) -> None:
        if not self.current or self._chat_busy():
            return
        openrouter = self.current.provider == OPENROUTER
        self.store.update_setup(self.current.id, OWNER_ID, system_prompt(self.config.profile_text(profile)),
                                tool_definitions(online_video=bool(self.current.source_url), web_search=openrouter,
                                                 # A llama.cpp chat keeps the extras chosen when it started.
                                                 local_web_search=not openrouter and self._local_web_search()),
                                # The chat keeps its own choice from Chat settings.
                                audio_only=self.current.audio_only)
        self.current = self.store.get_session(self.current.id, OWNER_ID)
        self.render_current()
        self.status.SetLabel(self.status.GetLabel() + f"\nPrompt profile set to {profile or NO_PROFILE}; tools enabled.")

    def update_system_prompt(self) -> None:
        """Gives the open chat the current version of its system prompt, keeping its profile, tools and transcript."""
        if not self.current or self._chat_busy():
            return
        updated = current_system_prompt(self.current.system_prompt)
        if updated == self.current.system_prompt or wx.MessageBox(
                "This chat still uses an earlier version of the system prompt. Update it to the current version? "
                "The chat keeps its prompt profile, tools and transcript. The next question sends the video uncached "
                "once, so it costs more.", "Update system prompt", wx.YES_NO | wx.ICON_QUESTION) != wx.YES:
            return
        self.store.update_system_prompt(self.current.id, OWNER_ID, updated)
        self.current = self.store.get_session(self.current.id, OWNER_ID)
        self.render_current()
        # The button disappears with the old version, so the focus goes on to the question.
        self.question.SetFocus()
        self.status.SetLabel(self.status.GetLabel() + "\nSystem prompt updated to the current version.")
        speech.announce("System prompt updated.")

    def _local_web_search(self) -> bool:
        """Whether the open llama.cpp chat was started with web search through OpenRouter."""
        return bool(self.current) and WEB_SEARCH in function_tool_names(self.current.tools)

    def chat_model(self, session: VideoSession) -> str:
        """The model a chat uses: its own, or the one in Settings for its provider if it has none ("default")."""
        return session.model or self.config.model_for(session.provider)

    def change_chat_settings(self) -> None:
        if not self.current or self._chat_busy() or getattr(self, '_loading_chat_settings', False):
            return
        session_id = self.current.id
        # Both providers' models, as the chat may move to the other one.
        catalogs = {OPENROUTER: self.catalog, LLAMA_CPP: None}
        if self.config.llama_cpp_url:
            try:
                catalogs[LLAMA_CPP] = llama_cpp_catalog(self.config.llama_cpp_url)
            except ValueError:
                pass
        self._loading_chat_settings = True
        self.chat_settings_button.Disable()
        self.status.SetLabel("Loading the model lists. Other chat windows remain available.")

        def model_ids(catalog: ModelCatalog | None) -> list[str]:
            try:
                return catalog.model_ids() if catalog else []
            except Exception:
                return []  # Manual model entry remains available when a list fails.

        def worker() -> None:
            models = {provider: model_ids(catalog) for provider, catalog in catalogs.items()}
            if wx.GetApp():
                wx.CallAfter(self._chat_settings_loaded, session_id, models)

        threading.Thread(target=worker, daemon=True).start()

    def _chat_settings_loaded(self, session_id: str, models: dict[str, list[str]]) -> None:
        if not self or self._closing:
            return
        self._loading_chat_settings = False
        self._set_chat_mutation_buttons()
        if not self.current or self.current.id != session_id or self._chat_busy():
            return
        session = self.current
        settings_models = {provider: self.config.model_for(provider) for provider in PROVIDER_LABELS}
        with ChatSettingsDialog(self, session.model, settings_models, session.audio_only, session.sent_video_size,
                                self.config.shrink_target_mb, models, provider=session.provider,
                                transcribe=session.transcribe) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            model, audio_only, shrink = dialog.value()
            provider, transcribe = dialog.provider_value()
        if (model, audio_only, provider, transcribe) != (
                session.model, session.audio_only, session.provider, session.transcribe):
            # Moving to the other provider keeps the chosen tools, with that provider's web search; a
            # llama.cpp chat searches through OpenRouter, so without its key it gets no search, as when starting.
            tools = tools_for_provider(session.tools, provider) if provider != session.provider else None
            if tools is not None and provider == LLAMA_CPP and not self.openrouter_key():
                tools = [tool for tool in tools if tool_key(tool) != WEB_SEARCH]
            self.store.update_chat_settings(session.id, OWNER_ID, model, audio_only, provider=provider,
                                            transcribe=transcribe, tools=tools)
        if shrink:
            self.shrink_stored_video()
        self.current = self.store.get_session(session.id, OWNER_ID)
        self.render_current()

    def shrink_stored_video(self) -> bool:
        """Replaces the open chat's stored video by a shrunk copy, made from the original file if it is still
        there. Returns False if cancelled or failed."""
        if not self.current or self._chat_busy():
            return False
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
        if not self.current or self._chat_busy():
            return False
        session = self.current
        link = youtube_link_in_file_name(session.source_path)
        if not link and is_local_file_chat(session.title):
            return True
        choices: list[tuple[str, str]] = []
        if link:
            choices.append((f"The video ID in the file name: {link}", link))
        speech.announce("Searching YouTube…")
        try:
            results = self._with_youtube_sign_in(
                lambda cookies: self._lookup_with_progress(search_videos, (session.title,), cookies))
        except RequestCancelled:
            return False
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
        speech.announce("Loading the video information…")
        try:
            info = self._with_youtube_sign_in(
                lambda cookies: self._lookup_with_progress(fetch_video_info, (url,), cookies))
        except RequestCancelled:
            return False
        except Exception as error:
            wx.MessageBox(f"The video information could not be loaded: {error}", "Link not used", wx.OK | wx.ICON_WARNING)
            return True
        self.store.update_source(session.id, OWNER_ID, url, info)
        self.current = self.store.get_session(session.id, OWNER_ID)
        return True

    def render_current(self) -> None:
        if not self.current:
            return
        session = self.current
        self.SetTitle(f"{session.title} - SceneAccess")
        context = "The local video context is stored; the original file is not required."
        source = "The original file is no longer available." if session.source_missing else "The original file is still available."
        model = session.model or f"{self.config.model_for(session.provider)} (from Settings)"
        self.status.SetLabel(f"{session.title} | {model} via {PROVIDER_LABELS.get(session.provider, session.provider)}"
                             f"\n{context} {source}")
        rows = chat_history_rows(session.initial_question, session.history, session.transcript_state, session.transcript,
                                 session.sections)
        items = [shown for shown, _full, _index, _kind in rows]
        self.history_items = items
        self.reader_texts = [full for _shown, full, _index, _kind in rows]
        # Which stored entry each item shows, and what it is: for the context menu and the selection.
        self.history_rows = [(index, kind) for _shown, _full, index, kind in rows]
        # The list is built anew, so a growing item of a streamed answer is no longer where it was.
        self._live_position = None
        self.chat_list.Set(items)
        # Select the newest answer rather than its usage line, so it is what gets read first.
        answers = [position for position, (_index, kind) in enumerate(self.history_rows) if kind == ANSWER]
        self.select_history_item(answers[-1] if answers else len(items) - 1)
        outdated = current_system_prompt(session.system_prompt) != session.system_prompt
        if self.prompt_button.IsShown() != outdated:
            self.prompt_button.Show(outdated)
            self.prompt_button.GetParent().Layout()
        unanswered = not session.history and bool(session.initial_question)
        if self.resend_button.IsShown() != unanswered:
            self.resend_button.Show(unanswered)
            self.resend_button.GetParent().Layout()
        self._set_chat_mutation_buttons()

    def on_history_menu(self, event: wx.ContextMenuEvent) -> None:
        """The context menu of the Chat history, for the selected items."""
        position = event.GetPosition()
        if position != wx.DefaultPosition:
            # A right click: the list does not select what was clicked, so select it first, unless it is part
            # of the selection. The keyboard (Applications key, Shift+F10) gives no position and means the selection.
            clicked = self.chat_list.HitTest(self.chat_list.ScreenToClient(position))
            if clicked != wx.NOT_FOUND and clicked not in self.chat_list.GetSelections():
                self.select_history_item(clicked)
        selected = list(self.chat_list.GetSelections())
        caret = self.history_caret()
        self.show_history_menu(selected or ([caret] if caret != wx.NOT_FOUND else []))

    def show_history_menu(self, positions: list[int]) -> None:
        """Copying works for any items; hiding and deleting for the answers' rounds among them. The first question
        and the transcript sent with the video belong to the video, not to a round."""
        positions = sorted(position for position in positions if 0 <= position < len(self.history_rows))
        if not self.current or not positions:
            return
        indices = sorted({self.history_rows[position][0] for position in positions
                          if self.history_rows[position][0] is not None})
        menu = wx.Menu()
        copy = menu.Append(wx.ID_ANY, "Copy to clipboard" if len(positions) == 1
                           else f"Copy {len(positions)} items to clipboard").GetId()
        toggle = delete = None
        hidden, rounds = False, 0
        if indices:
            rounds = len({round_bounds(self.current.history, index)[0] for index in indices})
            what = "this answer" if rounds == 1 else f"these {rounds} answers"
            hidden = all(self.current.history[index].get(EXCLUDED) for index in indices)
            toggle = menu.Append(wx.ID_ANY, f"Show {what} to the model again (cache miss from here on)" if hidden
                                 else f"Hide {what} from the model (cache miss from here on)").GetId()
            delete = menu.Append(wx.ID_ANY, f"Delete {what} from the history (cache miss from here on)").GetId()
            if self._chat_busy():
                # Shown but unavailable, so the screen reader says why nothing happens.
                menu.Enable(toggle, False)
                menu.Enable(delete, False)
        chosen = self.chat_list.GetPopupMenuSelectionFromUser(menu)
        menu.Destroy()
        if chosen == copy:
            self.copy_history_items(positions)
        elif chosen == toggle:
            self.set_answers_in_context(indices, hidden)
        elif chosen == delete:
            self.delete_answers(indices, rounds)

    def copy_history_items(self, positions: list[int]) -> None:
        """Copies the full text of the items, as the reader shows it, separated by blank lines."""
        text = "\n\n".join(self.reader_texts[position] for position in positions)
        if not wx.TheClipboard.Open():
            self.status.SetLabel("The clipboard is in use by another program. Please try again.")
            return
        try:
            wx.TheClipboard.SetData(wx.TextDataObject(text))
        finally:
            wx.TheClipboard.Close()
        message = "Copied." if len(positions) == 1 else f"Copied {len(positions)} items."
        self.status.SetLabel(self.status.GetLabel().split("\n")[0] + f"\n{message}")
        speech.announce(message)

    def set_answers_in_context(self, indices: list[int], in_context: bool) -> None:
        """Hides the answers' rounds (their questions, tool steps and answers) from the model, or shows them again."""
        if not self.current or self._chat_busy():
            return
        self.store.set_rounds_in_context(self.current.id, OWNER_ID, indices, in_context)
        self._history_changed(min(indices), "The model sees these answers again." if in_context else
                              "Hidden from the model; the answers stay in the history.")

    def delete_answers(self, indices: list[int], rounds: int) -> None:
        """Deletes the answers' rounds, with their questions, tool steps and images, from the history and the context."""
        if not self.current or self._chat_busy():
            return
        what = "This answer, its question and its tool steps" if rounds == 1 else \
            f"These {rounds} answers, their questions and their tool steps"
        if wx.MessageBox(f"{what} will be deleted from the chat, and the model will no longer see them.",
                         "Delete from the history?", wx.YES_NO | wx.ICON_WARNING) != wx.YES:
            return
        self.store.delete_rounds(self.current.id, OWNER_ID, indices)
        self._history_changed(min(indices), "Deleted from the history.")

    def add_transcript(self) -> None:
        """Transcribes the chat's speech through OpenRouter and adds it at the end of the chat, so the cached video
        and history before it stay valid. Asks the model nothing: that happens with the next question."""
        if not self.current or self._chat_busy():
            return
        session = self.current
        key = self.openrouter_key()
        if not key:
            wx.MessageBox("A transcript is made through OpenRouter. Enter an OpenRouter API key in Settings.",
                          "OpenRouter API key required", wx.OK | wx.ICON_ERROR)
            return
        control = RequestControl()
        client = ChatClient(key, control=control, ignored_providers=self.config.ignored_providers)
        if not client.transcription_allowed():
            wx.MessageBox("ElevenLabs, which makes the transcript, is excluded in Settings, Providers and models.",
                          "Speech recognition excluded", wx.OK | wx.ICON_ERROR)
            return
        self._request = control
        if self.windows:
            self.windows.requests[session.id] = control
        self._set_request_buttons(True)
        self.status.SetLabel("Transcribing the speech. It is added at the end of the chat; nothing is sent to the "
                             "model until you ask a question.")
        speech.announce("Transcribing the speech…")

        def worker() -> None:
            try:
                text = make_transcript(session, client)
                control.check_cancelled()
                self.store.append_transcript(session.id, OWNER_ID, text)
                wx.CallAfter(self._request_callback, control, self._transcript_added, bool(text))
            except RequestCancelled:
                wx.CallAfter(self._request_callback, control, self._cancelled)
            except Exception as error:
                wx.CallAfter(self._request_callback, control, self._failed, f"The speech could not be transcribed: {error}")
            finally:
                if self.windows:
                    wx.CallAfter(self.windows.request_finished, session.id, control)

        threading.Thread(target=worker, daemon=True).start()

    def _transcript_added(self, found_speech: bool) -> None:
        self._finish_request()
        self.current = self.store.get_session(self.current.id, OWNER_ID)
        self.render_current()
        if found_speech:
            self.select_history_item(len(self.history_items) - 1)
        message = ("The transcript was added at the end of the chat; the next question sends it." if found_speech
                   else "No speech was detected in the video.")
        self.status.SetLabel(self.status.GetLabel() + f"\n{message}")
        speech.announce(message)
        self.chat_list.SetFocus()

    def _history_changed(self, index: int, message: str) -> None:
        self.current = self.store.get_session(self.current.id, OWNER_ID)
        self.render_current()
        # Stay where the change happened rather than jumping to the newest answer.
        positions = [position for position, (row_index, _kind) in enumerate(self.history_rows)
                     if row_index is not None and row_index >= index]
        if positions:
            self.select_history_item(positions[0])
        self.status.SetLabel(self.status.GetLabel() + f"\n{message}")
        speech.announce(message)
        self.chat_list.SetFocus()

    def _chat_busy(self) -> bool:
        # A closed window's cancelled worker may still be committing local state.
        return bool(self._request or (self.windows and self.current
                                     and self.current.id in self.windows.requests))

    def _set_chat_mutation_buttons(self) -> None:
        busy = self._chat_busy()
        for button in (self.setup_button, self.prompt_button, self.update_path_button, self.transcript_button,
                       self.resend_button):
            button.Enable(not busy)
        self.chat_settings_button.Enable(not busy and not getattr(self, '_loading_chat_settings', False))

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

    def on_resend(self, _event: wx.CommandEvent | None) -> None:
        """Sends the stored first question again, unchanged; what is typed in the question field stays there."""
        if not self.current or self.current.history or not self.current.initial_question or self._request:
            return
        if not self._ask_about_stored_size():
            return
        self._send(self.current.initial_question, initial=True)
        # This button is disabled while the request runs and hidden once it is answered, which would leave the
        # focus nowhere and the screen reader silent; the send button, "Cancel request" while it runs, can stop it.
        self.send_button.SetFocus()

    def _send(self, question: str, *, initial: bool) -> None:
        if not self.current or self._request:
            return
        if self.windows and self.current.id in self.windows.requests:
            self.question.SetValue(question)
            wx.MessageBox("The previous request for this chat is still finishing. Please retry shortly.",
                          "Previous request is stopping", wx.OK | wx.ICON_INFORMATION)
            return
        openrouter_key = resolve_openrouter_key(self.config)
        llama_cpp = self.current.provider == LLAMA_CPP
        if llama_cpp and not self.config.llama_cpp_url:
            self.question.SetValue(question)
            wx.MessageBox("This chat runs on your own llama.cpp server. Enter its URL in Settings.",
                          "llama.cpp server URL required", wx.OK | wx.ICON_ERROR)
            return
        if not llama_cpp and not openrouter_key:
            self.question.SetValue(question)
            wx.MessageBox(answering.NO_OPENROUTER_KEY, "OpenRouter API key required", wx.OK | wx.ICON_ERROR)
            return
        session_id = self.current.id
        if initial:
            self.store.update_initial_question(session_id, OWNER_ID, question)
        control = self._request = RequestControl()
        if self.windows:
            self.windows.requests[session_id] = control
        config = self.config
        catalog = self.catalog
        self._pending_question = question
        self._set_request_buttons(True)
        self.status.SetLabel("Request in progress. The video is being sent from the private local context.")
        speech.announce("Generating answer…")

        if not initial:
            self._show_progress_item(session_id, f"You: {question}", announce_it=False)
        # The answer as it is streamed: one item per stretch of thinking or writing, whose text for the reader
        # grows with every piece. sections keeps every stretch, to show them if the answer is not stored.
        live = {"kind": "", "text": ""}
        sections: list[tuple[str, str]] = []

        def end_live() -> None:
            if live["kind"]:
                sections.append((live["kind"], live["text"]))
                live.update(kind="", text="")

        def on_delta(kind: str, text: str) -> None:
            if kind != live["kind"]:
                end_live()
                live["kind"] = kind
                wx.CallAfter(self._request_callback, control, self._start_live_item, session_id, kind)
            live["text"] += text
            wx.CallAfter(self._request_callback, control, self._update_live_item, session_id, kind, live["text"])

        def unfinished() -> list[tuple[str, str]]:
            return [*sections, *([(live["kind"], live["text"])] if live["kind"] and live["text"] else [])]

        def on_event(kind: str, text: str) -> None:
            if kind == "paused":
                wx.CallAfter(self._request_callback, control, self._paused, text)
                return
            # What was written before the tool call is complete; whatever comes after starts a new item.
            end_live()
            if kind == "section":
                # The section before is stored now, so after a failure it shows as saved, not as unfinished too.
                sections.clear()
            item = {"tool_call": f"Tool call: {text}", "tool_result": f"Tool result: {text}"}.get(kind, f"{text}…")
            sections.append(("step", item))
            wx.CallAfter(self._request_callback, control, self._show_progress_item, session_id, item)

        def transcript_step(session: VideoSession, wanted: bool, openrouter: ChatClient | None) -> VideoSession:
            # Made, or marked as not needed for now; the chat is read again to have the stored transcript.
            if wanted:
                self._prepare_transcript(session, openrouter)
                return self.store.get_session(session_id, OWNER_ID)
            if session.transcript_state == TRANSCRIPT_PENDING:
                self.store.update_transcript(session_id, OWNER_ID, TRANSCRIPT_NONE)
            return session

        def worker() -> None:
            runner = None
            try:
                request = answering.prepare(config, catalog, self.store.get_session(session_id, OWNER_ID), question,
                                            initial=initial, openrouter_key=openrouter_key, control=control,
                                            transcript_step=transcript_step)
                turn, runner = answering.answer(
                    request, on_event=on_event, on_delta=on_delta,
                    save_sections=lambda progress: self.store.save_sections(session_id, OWNER_ID, progress))
                control.check_cancelled()
                question_entry = [] if initial else [{"role": "user", "content": question}]
                self.store.record_turn(session_id, OWNER_ID, question_entry + turn.entries)
                if runner is not None:
                    runner.commit_files()
                completion = turn.completion
                wx.CallAfter(self._request_callback, control, self._completed, session_id, completion.model, completion.tools_unavailable)
            except RequestCancelled:
                wx.CallAfter(self._request_callback, control, self._cancelled, unfinished())
            except Exception as error:
                wx.CallAfter(self._request_callback, control, self._failed, str(error), unfinished())
            finally:
                if runner is not None:
                    runner.cleanup_files()
                if self.windows:
                    wx.CallAfter(self.windows.request_finished, session_id, control)

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
        self._set_chat_mutation_buttons()
        self.Layout()

    def on_pause(self, _event: wx.CommandEvent | None) -> None:
        if not self._request:
            return
        if self._request.pause_requested:
            self._request.resume()
            self.pause_button.SetLabel("Pause request")
            self.status.SetLabel("Request resumed.")
            speech.announce("Request resumed.")
        else:
            self._request.pause()
            self.pause_button.SetLabel("Resume request")
            self.status.SetLabel("The request will pause after the current round, including its local tools.")
            speech.announce("The request will pause after the current round.")

    def _paused(self, text: str) -> None:
        if self._request and self._request.pause_requested:
            self.pause_button.SetLabel("Resume request")
            self.status.SetLabel(text)
            speech.announce(text)

    def _finish_request(self, *, restore_question: bool = False) -> None:
        self._request = None
        if restore_question and not self.question.GetValue().strip():
            self.question.SetValue(self._pending_question)
        self._pending_question = ""
        self._set_request_buttons(False)

    def _cancelled(self, unfinished: list[tuple[str, str]] = ()) -> None:
        self.size_skip_after_answer = None
        self.size_confirm_after_answer = ""
        self._finish_request(restore_question=True)
        self._reload_current()
        self.render_current()
        self._show_unfinished(unfinished)
        self.status.SetLabel("Request cancelled. Completed chat history is preserved; provider charges may still apply.")
        speech.announce("Request cancelled.")

    def _reload_current(self) -> None:
        """Reads the open chat again: a request that stopped may have stored sections of its description."""
        if self.current:
            self.current = self.store.get_session(self.current.id, OWNER_ID)

    def _show_unfinished(self, unfinished: list[tuple[str, str]]) -> None:
        """Shows again what a request that was not stored had streamed, so it is not lost from view. It is not
        saved, so the next time the list is built, e.g. after another question, it is gone."""
        if not self.current or not unfinished:
            return
        for kind, text in unfinished:
            if kind == "thinking":
                shown, full, _kind = thinking_item(text, "Unfinished, not saved: model's thinking")
            elif kind == "text":
                shown = full = f"Unfinished, not saved: model wrote: {text}"
            else:
                shown = full = f"Unfinished, not saved: {text}"
            self._show_progress_item(self.current.id, shown, announce_it=False, full=full)

    def _prepare_transcript(self, session: VideoSession, client: ChatClient | None) -> None:
        """Runs in the request thread: transcribes the sound once, as the chat's transcript mode asks.
        A failure never stops the answer; the chat remembers it, so it is not paid for on every question.
        client is OpenRouter, also for a llama.cpp chat; None if no OpenRouter key is configured."""
        reason = ""
        if client is None:
            reason = ('Speech recognition skipped because no OpenRouter API key is configured. The model answers '
                      'without a transcript and cannot hear the video. After entering a key, Change prompt profile '
                      'and tools tries again.')
        elif not client.transcription_allowed():
            reason = ('Speech recognition skipped because ElevenLabs is excluded. '
                      'The model answers without a transcript and cannot hear the video. '
                      'After changing the exclusion, Change prompt profile and tools tries again.')
        if reason:
            self.store.update_transcript(session.id, OWNER_ID, TRANSCRIPT_FAILED, reason)
            shown, full = transcript_entry(TRANSCRIPT_FAILED, reason)
            wx.CallAfter(self._show_progress_item, session.id, shown, full=full)
            return
        wx.CallAfter(self._show_progress_item, session.id, "Transcribing the video's speech…")
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
        self.history_rows.append((None, NOTE))  # Not stored yet, so not part of a round.
        self.chat_list.Append(item)
        if announce_it:
            speech.announce(item, interrupt=False)

    def _start_live_item(self, session_id: str, kind: str) -> None:
        """Adds the item that shows the model's thinking or writing while it is streamed. Once the answer is
        stored, the Chat history shows the same as items of their own: the thinking folded, the text written
        before a tool call as a note, and the answer."""
        label = LIVE_LABELS.get(kind, kind)
        # The item's text stays as it is: changing it while the screen reader is on it would read it out again
        # and could move the list's caret. Only what Enter shows grows.
        self._show_progress_item(session_id, f"{label}… (Enter shows it so far)", announce_it=False)
        if self.current and self.current.id == session_id and not self._closing:
            self._live_position = len(self.history_items) - 1
            speech.announce(f"{label}…", interrupt=False)

    def _update_live_item(self, session_id: str, kind: str, text: str) -> None:
        position = getattr(self, "_live_position", None)
        if (self._closing or not self.current or self.current.id != session_id or position is None
                or position >= len(self.history_items)):
            return
        self.reader_texts[position] = f"{LIVE_LABELS.get(kind, kind)} (so far):\n\n{text}"

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
            speech.announce(self.history_items[self.history_caret()])
        else:
            self.chat_list.SetFocus()

    def _failed(self, error: str, unfinished: list[tuple[str, str]] = ()) -> None:
        self._finish_request(restore_question=True)
        self.size_skip_after_answer = None
        self.size_confirm_after_answer = ""
        self.send_button.Enable()
        # Nothing of the failed turn was stored but the sections already described; its progress items go, but
        # what it streamed stays in view.
        self._reload_current()
        self.render_current()
        self._show_unfinished(unfinished)
        self.status.SetLabel("The request failed. The chat and local video context are preserved.")
        provider = self.current.provider if self.current else OPENROUTER
        if provider == LLAMA_CPP and "exceeds the available context size" in error:
            error += ("\n\nThe video does not fit into the llama.cpp server's context. Start the server with a larger "
                      "-c, a lower --video-fps, or shrink the video in Chat settings.")
        wx.MessageBox(error, f"{PROVIDER_LABELS.get(provider, provider)} error", wx.OK | wx.ICON_ERROR)


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


def main() -> int:
    app = wx.App()
    app.SetAppName("SceneAccess")
    sys.excepthook = _report_unexpected_error
    instance = None
    try:
        path = config_path_from_standard_paths(wx.StandardPaths.Get())
        instance = SingleInstance(path.parent)
        if not instance.is_primary:
            if instance.activate_existing():
                return 0
            wx.MessageBox("SceneAccess is already running, but its window could not be activated. "
                          "Please switch to the existing instance.", "SceneAccess is already running", wx.OK | wx.ICON_WARNING)
            return 1
        config = load_config(path)  # Validate before constructing any application window.
        windows = ChatWindows()
        windows.config_path, windows.config = path, config
        windows.show_list()
        instance.start(lambda: wx.CallAfter(windows.activate))
        app.MainLoop()
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        wx.MessageBox(f"SceneAccess could not start.\n\n{error}", "SceneAccess startup error", wx.OK | wx.ICON_ERROR)
        return 1
    finally:
        if instance:
            instance.close()
        for window in wx.GetTopLevelWindows():
            window.Destroy()
        app.Destroy()
