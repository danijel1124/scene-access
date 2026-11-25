"""A chat's window: building it, its keys, and the Chat history list with its menu. Setting a chat up
is chat_setup.py, sending a question chat_request.py; ChatFrame puts them together."""

from __future__ import annotations

import ctypes
import sys
from typing import TYPE_CHECKING

import wx

from ..llm.models import ModelCatalog
from ..prompts import current_system_prompt
from ..providers import OPENROUTER_URL, PROVIDER_LABELS
from ..request_control import RequestControl
from ..settings.config import load_config
from ..storage.session_store import EXCLUDED, LEGACY_OWNER_ID, OWNER_ID, VideoSession, open_store, round_bounds
from . import speech
from .app_window import AppWindow, config_path
from .chat_dialogs import MessageReaderDialog
from .chat_history import ANSWER, chat_history_rows
from .chat_request import ChatRequestMixin
from .chat_setup import ChatSetupMixin
from .new_chat import NewChatMixin

if TYPE_CHECKING:
    from .app import ChatWindows

# Windows list box messages for the item the keyboard is on, which a multiple-selection list keeps apart.
LB_SETCARETINDEX, LB_GETCARETINDEX = 0x019E, 0x019F


class ChatFrame(AppWindow, NewChatMixin, ChatSetupMixin, ChatRequestMixin, wx.Frame):
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
            self.config_path = config_path()
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
