"""A chat window setting its chat up: the prompt profile and tools, the system prompt, Chat settings,
shrinking the stored video, finding the online source of a local file and adding a transcript later."""

from __future__ import annotations

import contextlib
import re
import shutil
import threading
from pathlib import Path

import wx

from ..llm.client import ChatClient
from ..llm.models import AUTO_ROUTER, ModelCatalog, llama_cpp_catalog
from ..llm.tool_calls import WEB_SEARCH
from ..media import transcript
from ..media.video import local_video
from ..media.video_size import SIZE_LIMIT, megabytes
from ..media.youtube import fetch_video_info, search_videos
from ..pipeline.tools import function_tool_names, tool_definitions, tool_key, tools_for_provider
from ..prompts import current_system_prompt, system_prompt
from ..providers import LLAMA_CPP, OPENROUTER, PROVIDER_LABELS
from ..request_control import RequestCancelled, RequestControl
from ..storage.session_store import OWNER_ID, VideoSession
from ..timecodes import format_time
from . import speech
from .chat_dialogs import SIZE_CHAT_SETTINGS, SIZE_SEND, SIZE_SHRINK, ChatSettingsDialog, StoredSizeDialog
from .dialogs import NO_PROFILE
from .new_chat import url_from_clipboard

# yt-dlp's default file name ends with the video ID in brackets: "Title [jNQXAC9IVRw].mp4".
YOUTUBE_ID_IN_NAME = re.compile(r"\[([A-Za-z0-9_-]{11})\]")


def youtube_link_in_file_name(source_path: str) -> str | None:
    found = YOUTUBE_ID_IN_NAME.search(Path(source_path).name)
    return f"https://www.youtube.com/watch?v={found.group(1)}" if found else None


def is_local_file_chat(title: str) -> bool:
    # Chats from a local file are titled with the file name, downloads with the video title.
    return Path(title).suffix.lower() in (".mp4", ".webm", ".mov")


class ChatSetupMixin:
    """Mixed into ChatFrame: setting up the chat it shows."""

    def on_update_path(self, _event: wx.CommandEvent) -> None:
        if not self.current or self._chat_busy():
            return
        with wx.FileDialog(self, "Choose the original video at its new location",
                           wildcard="Video (*.mp4;*.webm;*.mov)|*.mp4;*.webm;*.mov",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
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
        self.status.SetLabel(self.status.GetLabel()
                             + f"\nPrompt profile set to {profile or NO_PROFILE}; tools enabled.")

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
            with contextlib.suppress(ValueError):
                catalogs[LLAMA_CPP] = llama_cpp_catalog(self.config.llama_cpp_url)
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
        message = (f"Which YouTube video is \"{session.title}\"? With the link, the model can also load moments in "
                   "high resolution and read the video information.")
        with wx.SingleChoiceDialog(self, message, "Online source of this chat",
                                   [label for label, _url in choices]) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return False
            url = choices[dialog.GetSelection()][1]
        if url == "none":
            return True
        if url == "enter":
            with wx.TextEntryDialog(self, "YouTube link:", "Online source of this chat",
                                    url_from_clipboard()) as dialog:
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
            wx.MessageBox(f"The video information could not be loaded: {error}", "Link not used",
                          wx.OK | wx.ICON_WARNING)
            return True
        self.store.update_source(session.id, OWNER_ID, url, info)
        self.current = self.store.get_session(session.id, OWNER_ID)
        return True

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
                text = transcript.make_transcript(session, client)
                control.check_cancelled()
                self.store.append_transcript(session.id, OWNER_ID, text)
                wx.CallAfter(self._request_callback, control, self._transcript_added, bool(text))
            except RequestCancelled:
                wx.CallAfter(self._request_callback, control, self._cancelled)
            except Exception as error:
                wx.CallAfter(self._request_callback, control, self._failed,
                             f"The speech could not be transcribed: {error}")
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
