from __future__ import annotations

import os
import threading
from pathlib import Path

import wx

from .config import AppConfig, config_path_from_standard_paths, load_config, save_config
from .openrouter import OpenRouterClient
from .session_store import VideoSession, VideoSessionStore

OWNER_ID = "local:" + os.environ.get("USER", "user")


def cache_status_text(cached_tokens: int, prompt_tokens: int) -> str:
    if cached_tokens:
        return f"Cache hit confirmed: {cached_tokens:,} of {prompt_tokens:,} prompt tokens"
    return "No cache hit was confirmed. Processing the video again may incur costs."


def resolve_openrouter_key(config: AppConfig) -> str | None:
    return os.environ.get("OPENROUTER_API_KEY") or config.api_key or None


class SettingsDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, config: AppConfig):
        super().__init__(parent, title="OpenRouter settings")
        panel = wx.Panel(self)
        form = wx.FlexGridSizer(3, 2, 8, 8)
        form.AddGrowableCol(1, 1)
        self.model = wx.TextCtrl(panel, value=config.model)
        self.base_url = wx.TextCtrl(panel, value=config.base_url)
        self.api_key = wx.TextCtrl(panel, value=config.api_key, style=wx.TE_PASSWORD)
        for label, control in (("Model", self.model), ("Base URL", self.base_url), ("API key", self.api_key)):
            form.Add(wx.StaticText(panel, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            form.Add(control, 1, wx.EXPAND)
        help_text = wx.StaticText(panel, label="The key is stored in the local application configuration. OPENROUTER_API_KEY takes precedence.")
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(form, 1, wx.EXPAND | wx.ALL, 12)
        layout.Add(help_text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)
        panel.SetSizer(layout)
        layout.Fit(self)
        self.SetMinSize((620, -1))

    def value(self) -> AppConfig:
        return AppConfig(self.model.GetValue().strip(), self.base_url.GetValue().strip(), self.api_key.GetValue().strip())


class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="SceneAccess", size=(1100, 760))
        self.config_path = config_path_from_standard_paths(wx.StandardPaths.Get())
        self.config = load_config(self.config_path)
        self.store = VideoSessionStore(self.config_path.parent / "chats")
        self.current: VideoSession | None = None
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
        self.transcript = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        self.question = wx.TextCtrl(panel, style=wx.TE_MULTILINE)
        self.send_button = wx.Button(panel, label="Send question")
        right.Add(self.status, 0, wx.EXPAND | wx.ALL, 8)
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
        with wx.FileDialog(self, "Choose a local video", wildcard="Video (*.mp4;*.webm;*.mov)|*.mp4;*.webm;*.mov", style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            source = Path(dialog.GetPath())
        prompt_dialog = wx.TextEntryDialog(self, "First question about the video:", "Start a video chat", "Describe this video.")
        if prompt_dialog.ShowModal() != wx.ID_OK:
            return
        question = prompt_dialog.GetValue().strip()
        if not question:
            return
        self.current = self.store.create_session(owner_id=OWNER_ID, source_path=source, title=source.name, model=self.config.model, initial_question=question)
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
            self.store.update_source_path(self.current.id, OWNER_ID, Path(dialog.GetPath()))
        self.current = self.store.get_session(self.current.id, OWNER_ID)
        self.render_current()

    def on_delete(self, _event: wx.CommandEvent) -> None:
        if not self.current:
            return
        if wx.MessageBox("The conversation history and locally stored video data URL will be deleted.", "Delete this chat?", wx.YES_NO | wx.ICON_WARNING) != wx.YES:
            return
        self.store.delete_session(self.current.id, OWNER_ID)
        self.current = None
        self.transcript.Clear()
        self.status.SetLabel("Chat deleted.")
        self.refresh_sessions()

    def render_current(self) -> None:
        if not self.current:
            return
        session = self.current
        context = "The local video context is stored; the original file is not required."
        source = "The original file is no longer available." if session.source_missing else "The original file is still available."
        self.status.SetLabel(f"{session.title} | {session.model}\n{context} {source}\nCache status: unknown until the next provider response.")
        transcript = [f"You: {session.initial_question}"]
        for message in session.history:
            label = "You" if message["role"] == "user" else "Model"
            transcript.append(f"{label}: {message['content']}")
        self.transcript.SetValue("\n\n".join(transcript))

    def on_send(self, _event: wx.CommandEvent) -> None:
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

        def worker() -> None:
            try:
                session = self.store.get_session(session_id, OWNER_ID)
                messages = session.messages if initial else self.store.build_follow_up_messages(session_id, OWNER_ID, question)
                result = OpenRouterClient(api_key, base_url=self.config.base_url).complete(session, messages)
                if initial:
                    self.store.record_answer(session_id, OWNER_ID, result.answer)
                else:
                    self.store.record_follow_up(session_id, OWNER_ID, question, result.answer)
                wx.CallAfter(self._completed, session_id, result.answer, result.cached_tokens, result.prompt_tokens)
            except Exception as error:
                wx.CallAfter(self._failed, str(error))

        threading.Thread(target=worker, daemon=True).start()

    def _completed(self, session_id: str, _answer: str, cached_tokens: int, prompt_tokens: int) -> None:
        self.current = self.store.get_session(session_id, OWNER_ID)
        self.render_current()
        self.status.SetLabel(self.status.GetLabel() + "\n" + cache_status_text(cached_tokens, prompt_tokens))
        self.refresh_sessions()
        self.send_button.Enable()

    def _failed(self, error: str) -> None:
        self.send_button.Enable()
        self.status.SetLabel("The request failed. The chat and local video context are preserved.")
        wx.MessageBox(error, "OpenRouter error", wx.OK | wx.ICON_ERROR)


def main() -> None:
    app = wx.App()
    frame = MainFrame()
    frame.Show()
    app.MainLoop()
