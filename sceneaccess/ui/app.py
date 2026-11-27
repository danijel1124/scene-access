"""The app: its windows, the chat list and one window per open chat, which share their settings,
catalogue and store (ChatWindows), and its start (main)."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import wx

from ..llm.models import ModelCatalog
from ..providers import OPENROUTER_URL
from ..request_control import RequestControl
from ..settings.config import AppConfig, load_config
from ..storage.session_store import LEGACY_OWNER_ID, OWNER_ID, SessionNotFound, VideoSessionStore, open_store
from .app_window import config_path
from .chat_list import ChatListFrame
from .chat_window import ChatFrame
from .single_instance import SingleInstance


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
            self.config_path = config_path()
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
        path = config_path()
        instance = SingleInstance(path.parent)
        if not instance.is_primary:
            if instance.activate_existing():
                return 0
            wx.MessageBox("SceneAccess is already running, but its window could not be activated. "
                          "Please switch to the existing instance.", "SceneAccess is already running",
                          wx.OK | wx.ICON_WARNING)
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
