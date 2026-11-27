"""The main window: the list of chats, starting a new chat, Settings and deleting a chat. A chat opens in its
own chat window (chat_window.ChatFrame) with Enter or a double click; moving through the list opens nothing."""

from __future__ import annotations

from typing import Any

import wx

from ..llm.models import api_url
from ..providers import LLAMA_CPP
from ..settings.config import save_config
from ..storage.session_store import SessionNotFound
from .app_window import AppWindow
from .dialogs import SettingsDialog
from .new_chat import NewChatMixin


class ChatListFrame(AppWindow, NewChatMixin, wx.Frame):
    def __init__(self, windows: Any):
        """windows is the app.ChatWindows that holds the settings, the stored chats and the chat windows."""
        super().__init__(None, title="SceneAccess", size=(560, 640))
        self.windows = windows
        self.config_path = windows.config_path
        self.store = windows.store
        self.owner_id = windows.owner_id
        self._closing = False
        self.session_ids: list[str] = []
        # Chosen in the dialogs of a new chat, handed to the chat window it starts in.
        self.pending_size_skip = ""
        self.chat_transcribe = windows.config.transcribe
        self._build()
        self.refresh_sessions()
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_ACTIVATE, self.on_activate)

    def _build(self) -> None:
        panel = wx.Panel(self)
        layout = wx.BoxSizer(wx.VERTICAL)
        self.new_button = wx.Button(panel, label="New video chat")
        self.settings_button = wx.Button(panel, label="Settings")
        # The label directly before the list gives it its accessible name for screen readers.
        sessions_label = wx.StaticText(panel, label="Recent video chats (Enter opens a chat)")
        self.sessions = wx.ListBox(panel)
        self.delete_button = wx.Button(panel, label="Delete chat")
        self.status = wx.StaticText(panel, label="")
        for control, proportion in ((self.new_button, 0), (self.settings_button, 0), (sessions_label, 0),
                                    (self.sessions, 1), (self.delete_button, 0), (self.status, 0)):
            layout.Add(control, proportion, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        panel.SetSizer(layout)
        self.new_button.Bind(wx.EVT_BUTTON, self.on_new)
        self.settings_button.Bind(wx.EVT_BUTTON, self.on_settings)
        self.delete_button.Bind(wx.EVT_BUTTON, self.on_delete)
        self.sessions.Bind(wx.EVT_LISTBOX_DCLICK, lambda _event: self.open_selected_chat())
        # A native list box sends no double click for Enter, so Enter is handled before the panel sees it.
        self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)

    def on_char_hook(self, event: wx.KeyEvent) -> None:
        if not self.handle_enter(wx.Window.FindFocus(), event.GetKeyCode(), event.ShiftDown()):
            event.Skip()

    def handle_enter(self, focused: wx.Window | None, key_code: int, shift: bool) -> bool:
        """Enter on the list opens the selected chat."""
        if key_code not in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) or shift or focused is not self.sessions:
            return False
        self.open_selected_chat()
        return True

    def open_selected_chat(self) -> None:
        index = self.sessions.GetSelection()
        if index != wx.NOT_FOUND:
            self.windows.open_session(self.session_ids[index])

    def refresh_sessions(self, select: str | None = None) -> None:
        """Shows the stored chats, keeping the selected one selected unless select names another. An
        unchanged list is left alone: resetting it would make the screen reader lose its place."""
        sessions = self.store.list_sessions(self.owner_id)
        ids, titles = [session.id for session in sessions], [session.title for session in sessions]
        index = self.sessions.GetSelection()
        selected = select or (self.session_ids[index] if 0 <= index < len(self.session_ids) else None)
        if ids != self.session_ids or titles != self.sessions.GetStrings():
            self.session_ids = ids
            self.sessions.Set(titles)
        if selected in ids and self.sessions.GetSelection() != ids.index(selected):
            self.sessions.SetSelection(ids.index(selected))

    def on_settings(self, _event: wx.CommandEvent) -> None:
        dialog = SettingsDialog(self, self.config, self.catalog)
        if dialog.ShowModal() == wx.ID_OK:
            candidate = dialog.value()
            try:
                if not candidate.model:
                    raise ValueError("An OpenRouter model is required.")
                if candidate.provider == LLAMA_CPP and not candidate.llama_cpp_url:
                    raise ValueError("New chats use the llama.cpp server, so its URL is required.")
                if candidate.llama_cpp_url:
                    api_url(candidate.llama_cpp_url, "models")
            except ValueError as error:
                wx.MessageBox(str(error), "Invalid settings", wx.OK | wx.ICON_ERROR)
            else:
                self.config = candidate
                save_config(self.config_path, self.config)
                # Chats on "default" show the model from Settings, which may have changed.
                self.windows.settings_changed()
        dialog.Destroy()

    def on_delete(self, _event: wx.CommandEvent | None) -> None:
        """Deletes the chat selected in the list, closing its chat window first."""
        index = self.sessions.GetSelection()
        if index == wx.NOT_FOUND:
            return
        session_id = self.session_ids[index]
        # Also covers a closed window whose cancelled request is still saving.
        if session_id in self.windows.requests:
            wx.MessageBox("This chat is still answering or saving its last answer. Cancel the request or wait, "
                          "then delete the chat.", "Chat is busy", wx.OK | wx.ICON_INFORMATION)
            return
        if wx.MessageBox("The conversation history and locally stored video data URL will be deleted.", "Delete this chat?", wx.YES_NO | wx.ICON_WARNING) != wx.YES:
            return
        self.windows.close_session(session_id)
        try:
            self.store.delete_session(session_id, self.owner_id)
        except (OSError, SessionNotFound) as error:
            wx.MessageBox(f"The chat could not be completely deleted. You can retry deleting it.\n\n{error}",
                          "Delete failed", wx.OK | wx.ICON_ERROR)
            return
        self.windows.refresh()
        if self.session_ids:
            self.sessions.SetSelection(min(index, len(self.session_ids) - 1))
        self.status.SetLabel("Chat deleted.")
        self.sessions.SetFocus()

    def on_activate(self, event: wx.ActivateEvent) -> None:
        if event.GetActive():
            self.windows.last_active = self
        event.Skip()

    def on_close(self, event: wx.CloseEvent) -> None:
        """Closing the list ends the app: every chat window closes too, cancelling its request."""
        self._closing = True
        self.windows.close_all_chats()
        event.Skip()

    def _start_chat(self, *args, **kwargs) -> None:
        """Starts the chat in a new chat window, shown only once the chat exists."""
        frame = self.windows.new_chat_window()
        frame.pending_size_skip, self.pending_size_skip = self.pending_size_skip, ""
        frame.chat_transcribe = self.chat_transcribe
        NewChatMixin._start_chat(frame, *args, **kwargs)
        self.windows.chat_started(frame)
