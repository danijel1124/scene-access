from __future__ import annotations

import getpass
import os
import re
import shutil
import sys
import threading
import traceback
from dataclasses import replace
from functools import partial
from pathlib import Path
from urllib.request import urlopen

import wx

from .app_window import AppWindow, open_store, resolve_openrouter_key
from .chat_history import ANSWER, NOTE, chat_history_rows, transcript_entry
from .chat_list import ChatListFrame
from .config import AppConfig, config_path_from_standard_paths, load_config, save_config
from .chat_dialogs import (SIZE_CHAT_SETTINGS, SIZE_SEND, SIZE_SHRINK, ChatSettingsDialog, MessageReaderDialog,
                           StoredSizeDialog)
from .dialogs import NO_PROFILE
from .models import AUTO_ROUTER, ModelCatalog, routing
from .new_chat import NewChatMixin, url_from_clipboard
from .openrouter import OpenRouterClient
from .providers import LLAMA_CPP, OPENROUTER, OPENROUTER_URL, PROVIDER_LABELS
from .request_control import RequestControl, RequestCancelled
from .prompts import system_prompt
from .session_store import (EXCLUDED, TRANSCRIPT_DONE, TRANSCRIPT_FAILED, TRANSCRIPT_NONE, TRANSCRIPT_PENDING,
                            VideoSession, VideoSessionStore, SessionNotFound)
from .speech import announce
from .single_instance import SingleInstance
from .tools import (WEB_SEARCH, ToolResult, ToolRunner, format_time, function_tool_names, local_video,
                    provider_tools, tool_definitions, tool_key, tools_for_provider)
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


def _search_through_openrouter(client: OpenRouterClient, model: str, options: dict, query: str) -> ToolResult:
    """The web_search tool of a llama.cpp chat: OpenRouter's search, given only the query. options holds
    the provider and model exclusions from Settings."""
    text, sources = client.web_search(query, model, options)
    if sources:
        text += "\n\nSources:\n" + "\n".join(f"- {source['title']}: {source['url']}" for source in sources)
    count = len(sources)
    return ToolResult(text or "The search found nothing.",
                      caption=f"Searched the web for {query}: {count} source{'' if count == 1 else 's'}")


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
        self.chat_list = wx.ListBox(panel)
        self.question = wx.TextCtrl(panel, style=wx.TE_MULTILINE)
        self.send_button = wx.Button(panel, label="Send question")
        self.pause_button = wx.Button(panel, label="Pause request")
        self.pause_button.Hide()
        self.chat_settings_button = wx.Button(panel, label="Chat settings")
        self.setup_button = wx.Button(panel, label="Change prompt profile and tools")
        self.update_path_button = wx.Button(panel, label="Update original file path")
        layout.Add(self.status, 0, wx.EXPAND | wx.ALL, 8)
        layout.Add(history_label, 0, wx.LEFT | wx.RIGHT, 8)
        layout.Add(self.chat_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        layout.Add(wx.StaticText(panel, label="Question"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        layout.Add(self.question, 0, wx.EXPAND | wx.ALL, 8)
        requests = wx.BoxSizer(wx.HORIZONTAL)
        requests.Add(self.send_button, 0, wx.RIGHT, 8)
        requests.Add(self.pause_button, 0)
        layout.Add(requests, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        chat = wx.BoxSizer(wx.HORIZONTAL)
        for button in (self.chat_settings_button, self.setup_button, self.update_path_button):
            chat.Add(button, 0, wx.RIGHT, 8)
        layout.Add(chat, 0, wx.ALL, 8)
        panel.SetSizer(layout)
        self.update_path_button.Bind(wx.EVT_BUTTON, self.on_update_path)
        self.setup_button.Bind(wx.EVT_BUTTON, lambda _event: self.change_setup())
        self.chat_settings_button.Bind(wx.EVT_BUTTON, lambda _event: self.change_chat_settings())
        self.send_button.Bind(wx.EVT_BUTTON, self.on_send)
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

    def open_selected_message(self) -> None:
        index = self.chat_list.GetSelection()
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
                # A switched-off server must not hold up the dialog for long.
                catalogs[LLAMA_CPP] = ModelCatalog(self.config.llama_cpp_url,
                                                   opener=lambda request, timeout: urlopen(request, timeout=5))
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
                                openrouter_transcript=session.openrouter_transcript,
                                openrouter_key=bool(self.openrouter_key())) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            model, audio_only, shrink = dialog.value()
            provider, openrouter_transcript = dialog.provider_value()
        if (model, audio_only, provider, openrouter_transcript) != (
                session.model, session.audio_only, session.provider, session.openrouter_transcript):
            # Moving to the other provider keeps the chosen tools, with that provider's web search; a
            # llama.cpp chat searches through OpenRouter, so without its key it gets no search, as when starting.
            tools = tools_for_provider(session.tools, provider) if provider != session.provider else None
            if tools is not None and provider == LLAMA_CPP and not self.openrouter_key():
                tools = [tool for tool in tools if tool_key(tool) != WEB_SEARCH]
            self.store.update_chat_settings(session.id, OWNER_ID, model, audio_only, provider=provider,
                                            openrouter_transcript=openrouter_transcript, tools=tools)
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
        announce("Searching YouTube…")
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
        announce("Loading the video information…")
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
        rows = chat_history_rows(session.initial_question, session.history, session.transcript_state, session.transcript)
        items = [shown for shown, _full, _index, _kind in rows]
        self.history_items = items
        self.reader_texts = [full for _shown, full, _index, _kind in rows]
        # Which stored entry each item shows, and what it is: for the context menu and the selection.
        self.history_rows = [(index, kind) for _shown, _full, index, kind in rows]
        self.chat_list.Set(items)
        # Select the newest answer rather than its usage line, so it is what gets read first.
        answers = [position for position, (_index, kind) in enumerate(self.history_rows) if kind == ANSWER]
        self.chat_list.SetSelection(answers[-1] if answers else len(items) - 1)
        self._set_chat_mutation_buttons()

    def on_history_menu(self, event: wx.ContextMenuEvent) -> None:
        """The context menu of the Chat history: a round can be taken out of the context or deleted."""
        position = event.GetPosition()
        if position != wx.DefaultPosition:
            # A right click: the list does not select what was clicked, so select it first. The keyboard
            # (Applications key, Shift+F10) gives no position and means the selected item.
            clicked = self.chat_list.HitTest(self.chat_list.ScreenToClient(position))
            if clicked != wx.NOT_FOUND:
                self.chat_list.SetSelection(clicked)
        self.show_history_menu(self.chat_list.GetSelection())

    def show_history_menu(self, position: int) -> None:
        if not self.current or not 0 <= position < len(self.history_rows):
            return
        index = self.history_rows[position][0]
        if index is None:
            return  # The first question and the transcript belong to the video, not to a round.
        if self._chat_busy():
            self.status.SetLabel("The chat is answering. Its history can be changed once the answer is complete.")
            announce("The chat is still answering.")
            return
        in_context = not self.current.history[index].get(EXCLUDED)
        menu = wx.Menu()
        toggle = menu.Append(wx.ID_ANY, "Hide this answer from the model (cache miss from here on)" if in_context
                             else "Show this answer to the model again (cache miss from here on)").GetId()
        delete = menu.Append(wx.ID_ANY, "Delete this answer from the history (cache miss from here on)").GetId()
        chosen = self.chat_list.GetPopupMenuSelectionFromUser(menu)
        menu.Destroy()
        if chosen == toggle:
            self.set_answer_in_context(index, not in_context)
        elif chosen == delete:
            self.delete_answer(index)

    def set_answer_in_context(self, index: int, in_context: bool) -> None:
        """Takes the answer's round (its question, tool steps and the answer) out of the context or puts it back."""
        if not self.current or self._chat_busy():
            return
        self.store.set_round_in_context(self.current.id, OWNER_ID, index, in_context)
        self._history_changed(index, "The model sees this answer again." if in_context else
                              "The answer and its question are hidden from the model; they stay in the history.")

    def delete_answer(self, index: int) -> None:
        """Deletes the answer's round, with its question, tool steps and images, from the history and the context."""
        if not self.current or self._chat_busy():
            return
        if wx.MessageBox("This answer, its question and its tool steps will be deleted from the chat, and the "
                         "model will no longer see them.", "Delete this answer?", wx.YES_NO | wx.ICON_WARNING) != wx.YES:
            return
        self.store.delete_round(self.current.id, OWNER_ID, index)
        self._history_changed(index, "The answer was deleted.")

    def _history_changed(self, index: int, message: str) -> None:
        self.current = self.store.get_session(self.current.id, OWNER_ID)
        self.render_current()
        # Stay where the change happened rather than jumping to the newest answer.
        positions = [position for position, (row_index, _kind) in enumerate(self.history_rows)
                     if row_index is not None and row_index >= index]
        if positions:
            self.chat_list.SetSelection(positions[0])
        self.status.SetLabel(self.status.GetLabel() + f"\n{message}")
        announce(message)
        self.chat_list.SetFocus()

    def _chat_busy(self) -> bool:
        # A closed window's cancelled worker may still be committing local state.
        return bool(self._request or (self.windows and self.current
                                     and self.current.id in self.windows.requests))

    def _set_chat_mutation_buttons(self) -> None:
        busy = self._chat_busy()
        for button in (self.setup_button, self.update_path_button):
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
            wx.MessageBox("No API key is configured. Enter one in Settings or set OPENROUTER_API_KEY.", "OpenRouter API key required", wx.OK | wx.ICON_ERROR)
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
            runner = None
            try:
                # OpenRouter for the extras of a llama.cpp chat; never given the chat's video.
                openrouter = (OpenRouterClient(openrouter_key, control=control, ignored_providers=config.ignored_providers)
                              if openrouter_key else None)
                if llama_cpp:
                    # Only the llama.cpp key goes to the user's server, never the OpenRouter key.
                    client = OpenRouterClient(config.llama_cpp_api_key, base_url=config.llama_cpp_url, control=control)
                else:
                    client = openrouter
                # A chat on "default" asks the model that is in Settings now.
                session = self.store.get_session(session_id, OWNER_ID)
                follows_settings = not session.model
                session = replace(session, model=session.model or config.model_for(session.provider))
                # Reject impossible routing before any potentially billable preprocessing. A llama.cpp
                # server has no providers or router to choose from.
                options = {} if llama_cpp else routing(session.model, session.audio_only, catalog,
                                                       config.ignored_providers, config.excluded_models)
                if follows_settings and session.transcript_state == TRANSCRIPT_NONE and needs_transcript(session, catalog):
                    # Settings now name a model that cannot hear, so this chat needs a transcript after all.
                    session = replace(session, transcript_state=TRANSCRIPT_PENDING)
                if session.transcript_state == TRANSCRIPT_PENDING:
                    self._prepare_transcript(session, openrouter if llama_cpp else client, catalog)
                    session = self.store.get_session(session_id, OWNER_ID)
                    session = replace(session, model=session.model or config.model_for(session.provider))
                messages = session.messages if initial else self.store.build_follow_up_messages(session_id, OWNER_ID, question)
                # Excluded providers never answer, the search included.
                search_options = (routing(config.model, False, catalog, config.ignored_providers, config.excluded_models)
                                  if openrouter else {})
                runner = ToolRunner(session, config.youtube_cookies, control=control,
                                    web_search=(partial(_search_through_openrouter, openrouter, config.model,
                                                        search_options) if openrouter else None))
                turn = client.run_turn(replace(session, tools=provider_tools(session.tools, session.provider)), messages,
                                       runner.run, on_event, options, max_tool_rounds=config.max_tool_rounds)
                control.check_cancelled()
                question_entry = [] if initial else [{"role": "user", "content": question}]
                self.store.record_turn(session_id, OWNER_ID, question_entry + turn.entries)
                runner.commit_files()
                completion = turn.completion
                wx.CallAfter(self._request_callback, control, self._completed, session_id, completion.model, completion.tools_unavailable)
            except RequestCancelled:
                wx.CallAfter(self._request_callback, control, self._cancelled)
            except Exception as error:
                wx.CallAfter(self._request_callback, control, self._failed, str(error))
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

    def _prepare_transcript(self, session: VideoSession, client: OpenRouterClient | None,
                            catalog: ModelCatalog | None = None) -> None:
        """Runs in the request thread: transcribes the sound once if the chat's model cannot hear it.
        A failure never stops the answer; the chat remembers it, so it is not paid for on every question.
        client is OpenRouter, also for a llama.cpp chat; None if no OpenRouter key is configured."""
        if not needs_transcript(session, catalog or self.catalog):
            self.store.update_transcript(session.id, OWNER_ID, TRANSCRIPT_NONE)
            return
        reason = ""
        if session.provider == LLAMA_CPP and not session.openrouter_transcript:
            reason = ('No transcript: this chat runs on your own llama.cpp server, which sees only the pictures, and '
                      'transcription through OpenRouter was not chosen when the chat started, so the sound stays '
                      'private. The model cannot hear the video.')
        elif client is None:
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
        self.history_rows.append((None, NOTE))  # Not stored yet, so not part of a round.
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
