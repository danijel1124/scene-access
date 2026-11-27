"""A chat window sending a question: the request in its own thread through the pipeline
(pipeline/answering.py), pausing and cancelling it, the items that grow while the answer streams, and finishing."""

from __future__ import annotations

import threading
from dataclasses import replace

import wx

from ..llm.client import ChatClient
from ..media import transcript
from ..pipeline import answering
from ..providers import LLAMA_CPP, OPENROUTER, PROVIDER_LABELS
from ..request_control import RequestCancelled, RequestControl
from ..settings.config import resolve_openrouter_key, save_config
from ..storage.session_store import (OWNER_ID, TRANSCRIPT_DONE, TRANSCRIPT_FAILED, TRANSCRIPT_NONE,
                                     TRANSCRIPT_PENDING, VideoSession)
from . import speech
from .chat_history import NOTE, thinking_item, transcript_entry

# The items that grow while an answer is streamed.
LIVE_LABELS = {"thinking": "Model is thinking", "text": "Model is writing"}


class ChatRequestMixin:
    """Mixed into ChatFrame: asking the model and showing the answer."""

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
                # A cancel before the answer was whole raised in answering.answer; one while it was split into
                # moments cost only those, so the answer is stored (user's wish, 25.11.2026).
                question_entry = [] if initial else [{"role": "user", "content": question}]
                self.store.record_turn(session_id, OWNER_ID, question_entry + turn.entries)
                if runner is not None:
                    runner.commit_files()
                completion = turn.completion
                wx.CallAfter(self._request_callback, control, self._completed, session_id, completion.model,
                             completion.tools_unavailable, control.cancelled)
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
            text = transcript.make_transcript(session, client)
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

    def _completed(self, session_id: str, answered_by: str, tools_unavailable: bool = False,
                   cancelled: bool = False) -> None:
        """The answer is stored. cancelled: the request was cancelled while the answer was split into moments, which
        costs only those; the user is told so, as they asked for the cancel."""
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
        if cancelled:
            note += " Request cancelled while splitting the answer into moments: the answer is kept, without them."
            if self.IsActive():
                speech.announce("Request cancelled. The answer is kept.")
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
